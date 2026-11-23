#!/usr/bin/env python3
"""baleen / eval · 负例校验共享模块(方案 B)——negative_eval.py 与 ablation.py 共用。

设计真源:eval/runs/negative_queries_design_2026-11-23.md §4.2(preflight v2 三段式)。

三段式分工:
- **规则段**(check_rules,纯 strpos 秒级):absent 词全库=0 + 饵/素材词对在指定笔记在场 +
  有饵类必须带饵词对(CSV 手误防呆)。q19 死于「absent 词全 0 但主题泛内容入库」——
  absent 词是术语级判死,主题级由哨兵段守。
- **形态段**(check_form,纯内存比对;检索由 negative_eval 收集):每条真检索 top5 与快照比对——
  top1 跳出快照圈 / 饵(素材)笔记丢出 top5 / 饵丢 top1 即 flag。快照即预期:
  b06 吉他这类「饵字面在场但检索捞不到」的形态,如实建快照后天然不误报。
- **哨兵段**(collect_sentinels + check_sentinel_counts,纯 strpos):sentinel_words 计数
  与快照比对,增长即 flag。语义=「当前计数是快照,增长=主题疑似长出实质内容,先人工复核」
  ——与 absent 的判死规则分开。

bait_word 两种格式(向后兼容,§4.2):
- 旧:bait_note_id 单值 + bait_word 分号分隔(存量 7 条不动);
- 新:多值多笔记「note_id:word;note_id:word」(b02 起——LangChain 已 4 篇在场,单笔记太脆)。

快照(eval/queries/negative_form_snapshot.json):qid → {top5, top1, sentinels:{词:计数}, ts}。
写盘策略(apply_snapshot):新 qid/新哨兵词基线**总是**增量写入(不是洗白,是不写就会次次当新词);
已有基线**只有** refresh 且三段全绿才重写(有 flag 时刷新=把漂移形态固化成新基线,自我洗白)。

queries.csv 内嵌负例(方案 B:q20 留原处,正例集 q01-q18 口径不动):
负例特有列(absent/sentinel)在 queries.csv 没有家,校验参数住 EMBEDDED_NEGATIVES(qid 为键)。
ablation.py 加载 queries.csv 时对 kind=negative 行调 check_embedded(规则+哨兵段,
不引混合检索)——WARN 不挡正例评测,消灭 q19 式「内嵌负例零校验」盲区。
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]  # eval/
QUERIES_CSV = EVAL_DIR / "queries" / "queries.csv"
SNAPSHOT_JSON = EVAL_DIR / "queries" / "negative_form_snapshot.json"

OWNER_ID = 1  # 与 negative_eval.py/ablation.py 同口径

# ntype 八值枚举(§2.4 分类学)+ 旧值 missing/bait 向后兼容(存量 8 条不改值)
NTYPES = (
    "missing", "missing_far", "missing_near",
    "bait", "bait_nomination", "bait_depth", "bait_entity",
    "false_premise_in_kb", "false_premise_no_kb", "time_sensitive",
)

# 有饵类:饵(或纠错素材)词对必须在场;missing 类无饵(缺=正常)
BAIT_NTYPES = {
    "bait", "bait_nomination", "bait_depth", "bait_entity",
    "false_premise_in_kb", "false_premise_no_kb", "time_sensitive",
}

# queries.csv 内嵌负例的校验参数(方案 B):题面读 queries.csv(单一真源),
# absent/sentinel/expect 等负例特有字段住这里。q19 已退役(2026-11-23,f01 继承方向)。
EMBEDDED_NEGATIVES: dict[str, dict[str, str]] = {
    "q20": {
        "ntype": "missing_far",
        "intent": "远领域烹饪(queries.csv 内嵌,方案 B)",
        "bait_note_id": "",
        "bait_word": "",
        "answer_absent_words": "红烧肉;菜谱;烹饪;食材;调料",
        "sentinel_words": "",  # absent 五词已是判死哨,烹饪内容入库必中其一
        "expect_gate": "false",
        "expect_used_web": "true",
        "note": "2026-11-18 设计验证红烧肉/菜谱/烹饪/食材/调料全库=0;"
        "方案 B 留 queries.csv,校验参数在 _negcheck.EMBEDDED_NEGATIVES",
    },
}


# ---------------------------------------------------------------- 行解析


def parse_bait_pairs(row: dict) -> list[tuple[int, str]]:
    """解析饵(或纠错素材)词对 → [(note_id, word)]。

    多值格式「note_id:word;note_id:word」(含冒号)优先;旧格式 bait_note_id+bait_word 兼容。
    """
    word_field = (row.get("bait_word") or "").strip()
    if ":" in word_field:
        pairs = []
        for part in word_field.split(";"):
            part = part.strip()
            if not part or ":" not in part:
                continue
            nid_s, word = part.split(":", 1)
            if nid_s.strip().isdigit() and word.strip():
                pairs.append((int(nid_s), word.strip()))
        return pairs
    nid = (row.get("bait_note_id") or "").strip()
    if not nid or not nid.isdigit():
        return []
    return [(int(nid), w.strip()) for w in word_field.split(";") if w.strip()]


def load_embedded_rows() -> list[dict]:
    """queries.csv 内嵌负例(kind=negative 行)→ 补齐负例字段的完整行。

    题面读 queries.csv(单一真源);负例特有参数取 EMBEDDED_NEGATIVES;
    出现在 queries.csv 却没有校验参数的 negative 行直接报配置错(不许静默跳过)。
    """
    with QUERIES_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
        qrows = [r for r in csv.DictReader(fh) if (r.get("kind") or "").strip() == "negative"]
    out = []
    for r in qrows:
        qid = (r.get("qid") or "").strip()
        params = EMBEDDED_NEGATIVES.get(qid)
        if params is None:
            raise SystemExit(
                f"[error] queries.csv 内嵌负例 {qid} 在 EMBEDDED_NEGATIVES 没有校验参数——"
                "先补 _negcheck.EMBEDDED_NEGATIVES 再跑(q19 式零校验盲区不许重演)"
            )
        out.append({"qid": qid, "query": (r.get("query") or "").strip(), **params})
    return out


# ---------------------------------------------------------------- 三段式(纯逻辑段)


def check_form(forms: dict, snapshot: dict, rows: list[dict]) -> list[str]:
    """形态段比对(纯内存):forms={qid:{top5:[note_id],top1:id}} vs 快照。

    规则(信号=质变,噪声=rrf 名次抖动不 flag):
    - top1 跳出快照 top5 圈 → flag(missing 类核心规则:n01 的 top1 从手写噪音
      变成 TCP 笔记那天,就是 8461 类内容长出之时);
    - 饵(素材)笔记在快照 top5 内但已丢出当前 top5 → flag(检索形态漂移);
    - 饵占快照 top1(实体碰撞形态)但当前 top1 不是饵 → flag(饵丢 top1)。
    快照缺形态基线的 qid 不比(新条目由 apply_snapshot 写基线)。
    """
    problems: list[str] = []
    for row in rows:
        qid = row["qid"]
        form = forms.get(qid)
        snap = snapshot.get(qid)
        if form is None or snap is None or "top5" not in snap:
            continue  # 新条目:写基线,不比对
        top5, top1 = form["top5"], form["top1"]
        if top1 is not None and snap["top5"] and top1 not in snap["top5"]:
            problems.append(
                f"{qid}: 检索 top1 从快照圈 {snap['top5']} 跳到 #{top1}——"
                "命中形态漂移,人工复核(新内容长出/饵笔记被改)"
            )
        bait_ids = {nid for nid, _ in parse_bait_pairs(row)}
        lost = sorted(bait_ids & set(snap["top5"]) - set(top5))
        if lost:
            problems.append(
                f"{qid}: 饵/素材笔记 {lost} 从 top5 丢位——检索形态漂移,人工复核"
            )
        # 实体碰撞类强化:饵占快照 top1 就必须仍占 top1(只限 bait_entity——
        # 其他类饵丢位由上一条规则抓,饵在快照占 top1 很常见,不能当实体形态)
        if (
            row.get("ntype") == "bait_entity"
            and snap.get("top1") in bait_ids
            and top1 not in bait_ids
        ):
            problems.append(
                f"{qid}: 实体碰撞形态的饵笔记 #{snap['top1']} 丢 top1(当前 #{top1})——形态漂移,人工复核"
            )
    return problems


def check_sentinel_counts(
    rows: list[dict], counts: dict, snapshot: dict
) -> tuple[list[str], dict]:
    """哨兵段比对(纯内存):counts={qid:{词:当前计数}} vs 快照 sentinels。

    返回 (问题列表, 更新后的快照)。新 qid/新哨兵词由 apply_snapshot 写基线,这里只比对
    已有基线:计数增长 → flag(主题级覆盖守门员,q19 死因正是无人盯「MySQL 泛内容入库」)。
    """
    problems: list[str] = []
    for row in rows:
        qid = row["qid"]
        snap = snapshot.get(qid) or {}
        sent_snap: dict = snap.get("sentinels") or {}
        for word, n in (counts.get(qid) or {}).items():
            base = sent_snap.get(word)
            if base is None:
                continue  # 新哨兵词:写基线,不比对
            if n > base:
                problems.append(
                    f"{qid}: 哨兵词「{word}」计数 {base}→{n}——主题内容疑似长出,人工复核"
                    "(顺带提及=确认后 --refresh-snapshot 更新基线;"
                    "实质内容=改标签:升级 bait/false_premise 或转正例)"
                )
    return problems, snapshot


def apply_snapshot(
    snapshot: dict,
    forms: dict,
    counts: dict,
    rows: list[dict],
    *,
    refresh: bool,
) -> dict:
    """写盘决策:新 qid/新哨兵词基线总是写入;已有基线只有 refresh 才重写。

    调用方约定:refresh=True 只在三段全绿时传(有 flag 时刷新=把漂移形态固化成新基线,
    自我洗白——negative_eval.main 挡住这种情况)。
    """
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        qid = row["qid"]
        snap = snapshot.setdefault(qid, {})
        form = forms.get(qid)
        if form and (refresh or "top5" not in snap):
            snap["top5"] = form["top5"]
            snap["top1"] = form["top1"]
            snap["ts"] = now
        snap.setdefault("sentinels", {})
        for word, n in (counts.get(qid) or {}).items():
            if refresh or word not in snap["sentinels"]:
                snap["sentinels"][word] = n
    return snapshot


# ---------------------------------------------------------------- 三段式(DB 段)


async def check_rules(conn, rows: list[dict], owner_id: int = OWNER_ID) -> list[str]:
    """规则段:absent=0 + 饵/素材词对在场 + 有饵类防呆。返回问题列表(空=通过)。"""
    problems: list[str] = []
    for row in rows:
        qid = row["qid"]
        ntype = row.get("ntype") or ""
        if ntype not in NTYPES:
            problems.append(f"{qid}: 未知 ntype「{ntype}」——合法值见 _negcheck.NTYPES")
        # 有饵类必须带饵词对(CSV 手误防呆:漏填 bait_word 时这里先红)
        if ntype in BAIT_NTYPES and not parse_bait_pairs(row):
            problems.append(f"{qid}: ntype={ntype} 属有饵类但没有饵/素材词对——bait_word 漏填?")
        # 饵(素材)词对必须在指定笔记在场(语料漂移检测)
        for nid, word in parse_bait_pairs(row):
            n = await conn.fetchval(
                "SELECT count(*) FROM chunks c JOIN notes n ON c.note_id=n.id"
                " WHERE n.id=$1 AND n.status='active' AND strpos(c.text,$2)>0",
                nid, word,
            )
            if not n:
                problems.append(
                    f"{qid}: 饵词「{word}」已不在笔记#{nid}——语料漂移,先复核标签再跑"
                )
        # absent 词必须全库为 0(否则不是负例)
        for word in [w for w in (row.get("answer_absent_words") or "").split(";") if w.strip()]:
            n = await conn.fetchval(
                "SELECT count(*) FROM chunks c JOIN notes n ON c.note_id=n.id"
                " WHERE n.status='active' AND n.owner_id=$1 AND strpos(c.text,$2)>0",
                owner_id, word.strip(),
            )
            if n:
                problems.append(
                    f"{qid}: absent 词「{word}」在库里出现 {n} 次——语料已含该主题,负例标签失效"
                )
    return problems


async def collect_sentinels(
    conn, rows: list[dict], owner_id: int = OWNER_ID
) -> dict[str, dict[str, int]]:
    """哨兵段数据收集:每个 qid 的 sentinel_words 当前全库计数( strpos,秒级)。"""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        words = [w.strip() for w in (row.get("sentinel_words") or "").split(";") if w.strip()]
        counts: dict[str, int] = {}
        for word in words:
            counts[word] = await conn.fetchval(
                "SELECT count(*) FROM chunks c JOIN notes n ON c.note_id=n.id"
                " WHERE n.status='active' AND n.owner_id=$1 AND strpos(c.text,$2)>0",
                owner_id, word,
            )
        if counts:
            out[row["qid"]] = counts
    return out


# ---------------------------------------------------------------- 快照 IO


def load_snapshot() -> dict:
    if SNAPSHOT_JSON.exists():
        return json.loads(SNAPSHOT_JSON.read_text(encoding="utf-8"))
    return {}


def save_snapshot(snapshot: dict) -> None:
    SNAPSHOT_JSON.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------------------------------------------------------------- ablation 入口(方案 B)


async def check_embedded(dsn: str) -> list[str]:
    """ablation.py 入口:queries.csv 内嵌负例(kind=negative 行)规则+哨兵段一站式校验。

    只做 strpos 秒级检查,不引混合检索(形态段归 negative_eval.py——负例主入口)。
    快照基线增量落盘:新 qid/新哨兵词写当前值(不写就会次次当新词);已有基线不动、
    有 flag 也照写(写的只是新增基线,不是刷新)。
    """
    import asyncpg

    rows = load_embedded_rows()
    if not rows:
        return []
    snapshot = load_snapshot()
    conn = await asyncpg.connect(dsn)
    try:
        problems = await check_rules(conn, rows)
        counts = await collect_sentinels(conn, rows)
    finally:
        await conn.close()
    sent_problems, snapshot = check_sentinel_counts(rows, counts, snapshot)
    problems += sent_problems
    snapshot = apply_snapshot(snapshot, {}, counts, rows, refresh=False)
    save_snapshot(snapshot)
    return problems


def run_check_embedded(dsn: str) -> list[str]:
    """同步包装(ablation.py 的同步 main 用)。"""
    return asyncio.run(check_embedded(dsn))
