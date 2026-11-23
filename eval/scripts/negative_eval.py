#!/usr/bin/env python3
"""baleen / eval · 负例评测(negative queries)——「不该答的有没有乱答」考核面。

正例 20 query(eval/queries/queries.csv,方案 B 后 q01-q18+q20=19 行)考「找得准」(R@5);
本集考另一半行为,负例 25 条(negative_queries.csv 24 条 + queries.csv 内嵌 q20):
  missing 类:库里根本没有 → 期望联网兜底(used_web=True)或干净自答,**不许库内引用**
  bait 类:  字面相近但不相关 → 期望 [NO_KB]→联网兜底,**不许瞎引饵笔记**
  false_premise 类:问题埋假前提 → 引用库内**纠错素材**纠正前提,或联网纠错(f02);
  time_sensitive 类:数值随时间变 → 背景可引库、数值必须联网(t01/t02)

设计要点(对应 defects_ledger.md backlog #2 与「检索无相关性阈值」悬案的评测面;
负例集设计研究报告:eval/runs/negative_queries_design_2026-11-23.md——分类学/候选/
preflight v2 的设计真源):
- 负例以**行为**断言,不以检索名次断言——向量检索永远返回 top_k,负例名次无意义;
  有意义的是相关性闸(_has_relevant_hit)的去向与问答兜底行为。
- **preflight v2 三段式**(_negcheck.py 共享,方案 B;喂料循环每批跑一次):
  规则段(absent 词全库=0 + 饵/素材词对在场)/形态段(真检索 top5 比对
  negative_form_snapshot.json 快照:top1 跳圈/饵丢位/饵丢 top1 即 flag)/
  哨兵段(sentinel_words 计数增长即 flag——q19 死于主题级覆盖,absent 词抓不到这层)。
  --refresh-snapshot:全绿时把快照全量刷新为本次实测(复核虚惊后用);有 flag 拒绝刷新。
- 本地档(默认)不烧 LLM:真混合检索 + 真相关性闸,记录 gate 去向与 top 命中。
  gate 期望失配只记 WARN 不记 FAIL——负例的行为正确性最终由 live 档判定。
- --live 走产品 HTTP 链路(后端已启动 + token),断言 used_web 与引用纪律:
  负例不允许出现 note_id 引用(NO_KB 路径会清空 citations,契约如此)。

用法(必须用 backend venv,与 ablation.py 同法):
    cd backend && uv run python ../eval/scripts/negative_eval.py
    cd backend && uv run python ../eval/scripts/negative_eval.py \
        --live --api-url http://127.0.0.1:8000 --token <JWT>

输出:
    eval/runs/negative_report.md    本地档(每次覆写,只含本脚本的产出)
    eval/runs/negative_preds.csv    live 档逐条记录(--append 追加,可复跑积累)
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

EVAL_DIR = Path(__file__).resolve().parents[1]  # eval/
BACKEND_DIR = EVAL_DIR.parent / "backend"
for _p in (str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _negcheck  # noqa: E402  共享校验模块(方案 B;ablation.py 也用它)

NEGATIVE_CSV = EVAL_DIR / "queries" / "negative_queries.csv"
REPORT_MD = EVAL_DIR / "runs" / "negative_report.md"
PREDS_CSV = EVAL_DIR / "runs" / "negative_preds.csv"

OWNER_ID = 1  # 评测语料归属(与 ablation.py 同口径)
TOP_K = 5  # 与产品 chat 默认一致


# ---------------------------------------------------------------- 工具


def _pct(values: list, p: float):
    """线性插值百分位(时延 P50/P95 用;空列表返回 0)。"""
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


def load_negative_queries() -> list[dict]:
    """全量负例行:negative_queries.csv(24 条)+ queries.csv 内嵌 kind=negative(方案 B,q20)。

    内嵌行的负例特有参数(ntype/absent/sentinel/expect)住 _negcheck.EMBEDDED_NEGATIVES;
    题面从 queries.csv 读(单一真源)。qid 撞车直接报错(两处同 qid=口径打架)。
    """
    with NEGATIVE_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [
            {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]
    embedded = _negcheck.load_embedded_rows()
    clash = {r["qid"] for r in rows} & {r["qid"] for r in embedded}
    if clash:
        raise SystemExit(f"[error] qid 两处重复 {sorted(clash)}——negative_queries.csv 与 queries.csv 内嵌行不许同 qid")
    return rows + embedded


# ---------------------------------------------------------------- preflight v2 三段式 + 本地档


async def run_preflight_and_local(
    rows: list[dict], dsn: str, *, refresh: bool = False
) -> tuple[list[str], list[dict], int]:
    """preflight v2(规则+形态+哨兵)与本地档合跑——检索只跑一遍,两个考核面共用结果。

    返回 (preflight 问题列表, 本地档逐条结果, 语料笔记数)。
    快照写盘(_negcheck.apply_snapshot):新 qid/新哨兵词基线总是写入;已有基线
    只有 refresh 且**全绿**才重写(negative_eval.main 约定,防自我洗白)。
    """
    from app.api.qa import _has_relevant_hit  # 复用生产实现,不另写判据
    from app.service.retrieve import retrieve
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        note_count = await conn.fetchval(
            "SELECT count(*) FROM notes WHERE status='active' AND owner_id=$1", OWNER_ID
        )
        # 规则段 + 哨兵段数据收集(纯 strpos,秒级)
        problems = await _negcheck.check_rules(conn, rows)
        counts = await _negcheck.collect_sentinels(conn, rows)
    finally:
        await conn.close()

    # 真混合检索一遍:形态段收集(forms)+ 本地档判定(gate/verdict)共用
    forms: dict = {}
    out: list[dict] = []
    for row in rows:
        result = await retrieve(row["query"], top_k=TOP_K, owner_id=OWNER_ID)
        hits = result.hits
        top5 = list(dict.fromkeys(h.note_id for h in hits))
        forms[row["qid"]] = {"top5": top5, "top1": top5[0] if top5 else None}
        gate = _has_relevant_hit(
            [SimpleNamespace(parent_text=h.parent_text) for h in hits], row["query"]
        )
        expect_gate = row["expect_gate"].lower() == "true"
        # 严重级:失配只 WARN——负例的行为正确性由 live 档断言,本地只记录去向
        if gate == expect_gate:
            verdict = "PASS"
        elif row["ntype"] == "bait" or row["ntype"].startswith("bait_"):
            verdict = "WARN"  # 饵没进 top_k 或闸没中:联网兜底接管,行为仍正确,记录观察
        else:
            verdict = "WARN"  # 噪音触发进 KB 路径:行为由 [NO_KB] 兜底,需人眼看
        out.append(
            {
                **row,
                "gate": gate,
                "verdict": verdict,
                "n_hits": len(hits),
                "top_hits": [
                    {
                        "note_id": h.note_id,
                        "title": h.note_title,
                        "path": h.heading_path or "",
                        "rrf": round(h.rrf_score, 4),
                    }
                    for h in hits[:3]
                ],
            }
        )

    # 形态段 + 哨兵段比对(纯内存)
    snapshot = _negcheck.load_snapshot()
    problems += _negcheck.check_form(forms, snapshot, rows)
    sent_problems, snapshot = _negcheck.check_sentinel_counts(rows, counts, snapshot)
    problems += sent_problems
    snapshot = _negcheck.apply_snapshot(
        snapshot, forms, counts, rows, refresh=refresh and not problems
    )
    _negcheck.save_snapshot(snapshot)
    return problems, out, int(note_count or 0)


# ---------------------------------------------------------------- live 档


def _set_agent_enabled(api_url: str, token: str, enabled: bool) -> bool:
    """翻转问答 agent 灰度开关(PUT /api/settings),返回**原值**(跑完恢复用)。"""
    with urllib.request.urlopen(
        urllib.request.Request(
            api_url.rstrip("/") + "/api/settings", headers={"Authorization": f"Bearer {token}"}
        ),
        timeout=15,
    ) as resp:
        previous = bool(json.loads(resp.read().decode("utf-8")).get("agent_enabled"))
    if previous != enabled:
        body = json.dumps({"agent_enabled": enabled}).encode("utf-8")
        req = urllib.request.Request(
            api_url.rstrip("/") + "/api/settings",
            data=body,
            method="PUT",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    return previous


def run_live(rows: list[dict], api_url: str, token: str, *, agent: bool = False) -> list[dict]:
    """产品 HTTP 链路:断言 used_web 与引用纪律。逐条写 negative_preds.csv(追加)。"""
    # 本机直连:合并 no_proxy,防系统代理拦截 127.0.0.1
    for var in ("NO_PROXY", "no_proxy"):
        merged = {p for p in os.environ.get(var, "").split(",") if p}
        merged |= {"localhost", "127.0.0.1"}
        os.environ[var] = ",".join(sorted(merged))

    # 预检:后端必须已启动(10061 连接拒绝 = 8000 端口没人监听 = uvicorn 没起)
    try:
        with urllib.request.urlopen(api_url.rstrip("/") + "/healthz", timeout=5) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001 启动问题统一给人话提示
        print(f"[error] 后端不可达({api_url}):{exc}")
        print("        先在另一个窗口启动后端并保持开着:")
        print("        cd baleen\\backend ; uv run uvicorn app.main:app --port 8000")
        print("        等出现 Uvicorn running + Loading weights 391/391(bge 预热)后,再重跑本脚本")
        raise SystemExit(2) from exc

    out: list[dict] = []
    session_id = None  # 8 条共用一个会话,别在主人侧栏炸出 8 个
    for row in rows:
        body = json.dumps(
            {"question": row["query"], "top_k": TOP_K, "session_id": session_id}
        ).encode("utf-8")
        req = urllib.request.Request(
            api_url.rstrip("/") + "/api/qa/chat",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        rec: dict = {"qid": row["qid"], "ntype": row["ntype"], "ts": datetime.now().isoformat()}
        try:
            started = time.perf_counter()
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rec["latency_ms"] = int((time.perf_counter() - started) * 1000)
            session_id = payload.get("session_id") or session_id
            cited_note_ids = sorted(
                {
                    c["note_id"]
                    for c in payload.get("assistant_message", {}).get("citations", [])
                    if c.get("note_id") is not None
                }
            )
            used_web = bool(payload.get("used_web"))
            rec.update(
                {
                    "used_web": used_web,
                    "search_query": payload.get("search_query", ""),
                    "cited_note_ids": ";".join(map(str, cited_note_ids)),
                    "answer_head": payload.get("assistant_message", {})
                    .get("content", "")[:80]
                    .replace("\n", " "),
                }
            )
            # agent 档新增指标(ADR 0010):每答 LLM 调用次数 / 工具调用分布 / 缓存计数。
            # 每行都写全这些键(CSV 的 fieldnames 取自第一行,键不齐会 DictWriter 报错)。
            assistant_msg = payload.get("assistant_message", {})
            trace = assistant_msg.get("agent_trace")
            rec["agent"] = bool(trace)
            if trace:
                rec["llm_calls"] = sum(
                    1 for s in trace if s.get("kind") in ("llm", "repair")
                )
                tools: dict[str, int] = {}
                for s in trace:
                    if s.get("kind") == "tool" and s.get("tool"):
                        tools[s["tool"]] = tools.get(s["tool"], 0) + 1
                rec["tool_calls"] = (
                    ",".join(f"{k}x{v}" for k, v in sorted(tools.items())) or "-"
                )
            else:
                rec["llm_calls"] = ""
                rec["tool_calls"] = ""
            rec["cache_hit_tokens"] = assistant_msg.get("cache_hit_tokens", 0)
            rec["cache_miss_tokens"] = assistant_msg.get("cache_miss_tokens", 0)
            # 判决:真混合作答 = 库内引用 且 未联网(答案内容由 [n] 背书 = 瞎引),两类档共用。
            # agent 档例外(ADR 0010,2026-11-20 首跑发现,逐条复核过):答案主体已联网([W n])
            # 时,库内 [n] 允许出现在「背景句真归因」——开头明示「笔记库里只有 X,以下答案
            # 来自联网」,[n] 只标笔记里确实有的背景。这与 11-18 方案 A 禁的「用自身知识
            # 补全答案再挂 [n] 引用背书」不同:那是拿 [n] 给答案内容背书,这是诚实归因。
            # 记 PASS,answer_head 留档供人工抽查;决策树档不受影响(web 答案天然无库内 [n])。
            if cited_note_ids and not used_web:
                rec["verdict"] = "FAIL"
                rec["reason"] = f"库内引用了笔记 {cited_note_ids}——负例不允许库内引用(瞎引)"
            elif used_web:
                rec["verdict"] = "PASS"
                if cited_note_ids:
                    rec["reason"] = (
                        f"联网兜底接管;背景句含库内真归因 {cited_note_ids}"
                        "(agent 档合法,人工抽查 answer_head)"
                    )
                else:
                    rec["reason"] = "联网兜底接管"
            else:
                rec["verdict"] = "WARN"
                rec["reason"] = "未联网(Tavily 未配时自答是合法路径),答案开头需人工过目"
        except urllib.error.HTTPError as exc:
            rec["verdict"] = "ERROR"
            rec["reason"] = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"
        except Exception as exc:  # noqa: BLE001 单条失败不中断
            rec["verdict"] = "ERROR"
            rec["reason"] = f"{type(exc).__name__}: {exc}"
        out.append(rec)
        print(f"  [live] {rec['qid']} {rec['verdict']} {rec.get('reason', '')}")

    # 追加落盘(复跑积累)
    if out:
        exists = PREDS_CSV.exists()
        with PREDS_CSV.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            if not exists:
                writer.writeheader()
            writer.writerows(out)
    return out


# ---------------------------------------------------------------- 报告


def write_report(local_rows: list[dict], note_count: int, live_rows: list[dict]) -> None:
    lines = [
        "# 负例评测报告(negative_report)",
        "",
        f"> 生成:{datetime.now().isoformat(timespec='seconds')} · 语料:owner {OWNER_ID} active {note_count} 篇 ·"
        f" 负例 {len(local_rows)} 条([negative_queries.csv](queries/negative_queries.csv)"
        " + queries.csv 内嵌 kind=negative 行,方案 B)",
        "",
        "## 考核面",
        "",
        "正例 20 query 考「找得准」(R@5);本集考「不该答的有没有乱答」——",
        "missing 类期望联网兜底/干净自答,bait 类期望 [NO_KB]→联网,",
        "false_premise 类期望引用库内**纠错素材**纠正前提或联网纠错(f02),time_sensitive 类背景可引库、数值必须联网。",
        "**真混合作答 = 库内引用 且 未联网**(答案内容由 [n] 背书 = 瞎引),决策树/agent 两档同判 FAIL。",
        "agent 档(ADR 0010,2026-11-20)例外:答案主体已联网([W n])时,开头明示「笔记库里只有 X、",
        "以下来自联网」的**背景句库内真归因**记 PASS(answer_head 留档人工抽查);决策树档 web 答案天然无库内引用。",
        "gate = 生产 `_has_relevant_hit`(qa.py)对真混合检索 top5 的判定;本地档失配只记 WARN。",
        "",
        "> ⚠️ agent 批(ADR 0010)怎么读:agent 只改「检索之后怎么决策**,**不改检索本身**——",
        "> 首轮检索与现状同链路同参数,20 题 R@5 不会因 agent 变化(升也不会)。",
        "> 本批的胜负手在**负例/边缘题的工具路径正确率**(换词再搜→判定答不了→转联网)",
        "> 与引用纪律(不许假引用),R@5 不是本批的考核面。",
        "",
        "## preflight(v2 三段式,自校验)",
        "",
        "规则段:饵/纠错素材词对在指定笔记在场 + absent 词全库=0 + 有饵类防呆;",
        "形态段:真检索 top5 与 [negative_form_snapshot.json](queries/negative_form_snapshot.json) 快照比对——"
        "top1 跳出快照圈/饵笔记丢位/饵丢 top1 即 flag;",
        "哨兵段:sentinel_words 计数增长即 flag(主题级覆盖守门员,q19 死因)。",
        "q20(queries.csv 内嵌,方案 B)同样纳入三段校验;ablation.py 跑正例集时对 kind=negative 行"
        "执行规则+哨兵段(WARN 不挡)。见运行输出;三段全绿才继续。",
        "设计真源:[negative_queries_design_2026-11-23.md](runs/negative_queries_design_2026-11-23.md) §4。",
        "",
        "## 本地档(真检索 + 真闸,无 LLM)",
        "",
        "| qid | 类型 | 期望gate | 实际gate | 判决 | top1 命中 |",
        "|---|---|---|---|---|---|",
    ]
    for r in local_rows:
        top1 = r["top_hits"][0] if r["top_hits"] else "无命中"
        hit_str = (
            f"#{top1['note_id']} {top1['title'][:14]}({top1['rrf']})" if isinstance(top1, dict) else top1
        )
        lines.append(
            f"| {r['qid']} | {r['ntype']} | {r['expect_gate']} | {r['gate']} | {r['verdict']} | {hit_str} |"
        )

    lines += [
        "",
        "### 逐条 top 命中(眼睛看)",
        "",
    ]
    for r in local_rows:
        lines.append(f"- **{r['qid']}** {r['query']}")
        for h in r["top_hits"]:
            path = h["path"][:40] if h["path"] else ""
            lines.append(f"  - #{h['note_id']} {h['title'][:20]} · {path} · rrf={h['rrf']}")

    if live_rows:
        lines += [
            "",
            "## live 档(产品 HTTP 链路,真 LLM)",
            "",
            "| qid | used_web | 库内引用 | 判决 | 说明 |",
            "|---|---|---|---|---|",
        ]
        for r in live_rows:
            lines.append(
                f"| {r['qid']} | {r.get('used_web', '')} | {r.get('cited_note_ids', '') or '—'}"
                f" | {r.get('verdict', '')} | {r.get('reason', '')[:40]} |"
            )
        # 两种判据的读数都保留(可复核):严格口径(任何库内引用即 FAIL)下,
        # 「联网 + 背景真归因」的行会改判——主人可按会话原文抽查后定夺
        refined = [
            r
            for r in live_rows
            if r.get("cited_note_ids") and r.get("used_web") and r.get("verdict") == "PASS"
        ]
        if refined:
            lines += [
                "",
                f"**判据注记**:严格判据(任何库内引用即 FAIL)下本批有 {len(refined)} 条会改判;"
                "agent 档按「答案主体联网 [W n] + 背景句库内真归因」记 PASS"
                "(2026-11-20 首跑逐条复核均为诚实归因,answer_head/会话原文留档可查,见考核面注)。",
            ]
        # agent 档指标(ADR 0010):每答调用次数 / 工具分布 / 时延 / 缓存(本批胜负手,见考核面注)
        agent_rows = [r for r in live_rows if r.get("agent")]
        if agent_rows or any("llm_calls" in r for r in live_rows):
            lines += [
                "",
                "### agent 档指标(本批新增)",
                "",
                "| qid | agent | llm_calls | 工具分布 | 时延ms | 缓存hit/miss |",
                "|---|---|---|---|---|---|",
            ]
            for r in live_rows:
                if "llm_calls" not in r:
                    continue  # 旧格式行(升级前的历史跑批)没有这些列
                lines.append(
                    f"| {r['qid']} | {'✓' if r.get('agent') else '—'} | {r.get('llm_calls') or '—'}"
                    f" | {r.get('tool_calls') or '—'} | {r.get('latency_ms', '')}"
                    f" | {r.get('cache_hit_tokens', 0)}/{r.get('cache_miss_tokens', 0)} |"
                )
            lats = [r["latency_ms"] for r in live_rows if r.get("latency_ms") is not None]
            calls = [int(r["llm_calls"]) for r in live_rows if str(r.get("llm_calls") or "").isdigit()]
            if lats or calls:
                lines += ["", f"- P50/P95 时延:{_pct(lats, 50)} / {_pct(lats, 95)} ms(本批 {len(lats)} 条)"]
            if calls:
                lines += [
                    f"- 每答 LLM 调用次数(agent 档):min/中位/max = {min(calls)}/{sorted(calls)[len(calls) // 2]}/{max(calls)}",
                    f"- 合计 {sum(calls)} 次 / {len(calls)} 答(决策树档基线 = 每答 1 次)",
                ]
            for label, rows_ in (("agent 档", agent_rows), ("决策树档", [r for r in live_rows if not r.get("agent")])):
                hits = sum(r.get("cache_hit_tokens") or 0 for r in rows_)
                misses = sum(r.get("cache_miss_tokens") or 0 for r in rows_)
                total = hits + misses
                if rows_ and total:
                    lines += [
                        f"- {label}缓存命中率:Σhit/Σ(hit+miss) = {hits}/{total} = {hits / total:.1%}"
                    ]
    else:
        lines += [
            "",
            "## live 档",
            "",
            "未跑。后端启动后:`uv run python ../eval/scripts/negative_eval.py --live"
            " --api-url http://127.0.0.1:8000 --token <JWT>`(结果追加进 negative_preds.csv)。",
        ]

    n_pass = sum(1 for r in local_rows if r["verdict"] == "PASS")
    n_warn = sum(1 for r in local_rows if r["verdict"] == "WARN")
    lines += [
        "",
        "## 小结",
        "",
        f"本地档 PASS {n_pass}/{len(local_rows)}(WARN {n_warn});"
        + (
            f"live 档 FAIL {sum(1 for r in live_rows if r.get('verdict') == 'FAIL')}/{len(live_rows)}。"
            if live_rows
            else "live 档未跑——行为正确性(used_web/引用纪律)以 live 档为准。"
        ),
        "",
        "对应:defects_ledger.md backlog #2、HANDOFF「检索无相关性阈值」悬案的评测面。",
        "",
    ]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 入口


def main() -> int:
    import argparse

    # 防呆:uv run 必须从 baleen\backend 起(那里有 pyproject.toml);别处起会退回系统 Python,
    # 缺 asyncpg/torch 等。先给一句人话提示,而不是甩一堆 traceback。
    try:
        import asyncpg  # noqa: F401  backend venv 才有
    except ModuleNotFoundError:
        print("[error] 当前 Python 缺 asyncpg——多半没用 backend 虚拟环境。")
        print("        uv run 必须从 baleen\\backend 目录起(那里有 pyproject.toml):")
        print("        cd baleen\\backend")
        print("        uv run python ..\\eval\\scripts\\negative_eval.py")
        print("        (或先激活 venv: .venv\\Scripts\\Activate.ps1 再 python ..\\eval\\scripts\\negative_eval.py)")
        return 2

    ap = argparse.ArgumentParser(description="负例评测:行为断言(preflight v2 三段式+真检索+真闸)")
    ap.add_argument(
        "--refresh-snapshot",
        action="store_true",
        help="全绿时把形态/哨兵快照全量刷新为本次实测(复核虚惊后用);有 flag 时拒绝刷新",
    )
    ap.add_argument("--live", action="store_true", help="追加跑产品 HTTP 链路(需后端+token)")
    ap.add_argument(
        "--agent",
        action="store_true",
        help="live 档显式确保 agent 开(ADR 0010;agent 已默认开,迁移 0017;与 --no-agent 互斥)",
    )
    ap.add_argument(
        "--no-agent",
        action="store_true",
        help="live 档临时关掉 agent 开关,跑旧决策树基线(A/B 对照;跑完恢复原值)",
    )
    ap.add_argument("--api-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=os.environ.get("BALEEN_EVAL_TOKEN", ""))
    args = ap.parse_args()

    rows = load_negative_queries()
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["ntype"]] = by_type.get(r["ntype"], 0) + 1
    type_desc = " / ".join(f"{v} {k}" for k, v in sorted(by_type.items()))
    print(f"负例集: {len(rows)} 条({type_desc})")

    from app.core.config import get_settings

    dsn = get_settings().database_url.replace("+asyncpg", "")

    print("[1/2] preflight v2 三段式(规则+形态+哨兵)· 含本地档真检索+真闸……")
    problems, local_rows, note_count = asyncio.run(
        run_preflight_and_local(rows, dsn, refresh=args.refresh_snapshot)
    )
    for p in problems:
        print(f"  [FAIL] {p}")
    if problems:
        if args.refresh_snapshot:
            print("  --refresh-snapshot:有 flag,已有基线未刷新——"
                  "先复核(改标签或确认虚惊),全绿后再跑一次。")
        print("preflight 未过:语料漂移或坏标签,先复核 negative_queries.csv 再跑。", file=sys.stderr)
        return 1
    print("  preflight 三段全绿"
          + ("(快照已全量刷新为本次实测)" if args.refresh_snapshot else ""))
    for r in local_rows:
        print(f"  [local] {r['qid']} gate={r['gate']}(期望 {r['expect_gate']}) {r['verdict']}")

    live_rows: list[dict] = []
    if args.live:
        if not args.token:
            print("[error] --live 需要 --token 或环境变量 BALEEN_EVAL_TOKEN", file=sys.stderr)
            return 2
        # agent 旗(ADR 0010;默认已开,迁移 0017):--agent 显式确保开 / --no-agent 临时关
        # 跑旧决策树基线(A/B)。翻转都记录原值、跑完恢复——评测不改变用户的持久设置。
        if args.agent and args.no_agent:
            print("[error] --agent 与 --no-agent 互斥", file=sys.stderr)
            return 2
        target: bool | None = True if args.agent else (False if args.no_agent else None)
        prev_agent: bool | None = None
        if target is True:
            print("[2/2] live 档(产品 HTTP 链路,--agent 显式确保 agent 开)……")
            prev_agent = _set_agent_enabled(args.api_url, args.token, True)
        elif target is False:
            print("[2/2] live 档(产品 HTTP 链路,--no-agent 临时关 agent=旧决策树基线)……")
            prev_agent = _set_agent_enabled(args.api_url, args.token, False)
        else:
            print("[2/2] live 档(产品 HTTP 链路,按当前设置跑——agent 默认开)……")
        try:
            live_rows = run_live(rows, args.api_url, args.token)
        finally:
            if target is not None and prev_agent is not None and prev_agent != target:
                _set_agent_enabled(args.api_url, args.token, prev_agent)
                print(f"  agent 开关已恢复原值({'开' if prev_agent else '关'})")
    else:
        print("[2/2] live 档跳过(--live 未开)")

    write_report(local_rows, note_count, live_rows)
    n_fail = sum(1 for r in live_rows if r.get("verdict") == "FAIL")
    print(f"[ok] 报告 → {REPORT_MD};本地 PASS {sum(1 for r in local_rows if r['verdict'] == 'PASS')}"
          f"/WARN {sum(1 for r in local_rows if r['verdict'] == 'WARN')}"
          + (f";live FAIL {n_fail}" if live_rows else ";live 未跑"))
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
