#!/usr/bin/env python
"""切块尺度消融实验(ADR 0004 §8):200 / 300 / 400 三档子块尺度 → 纯内存向量检索 → Recall@5/@10/MRR。

    cd backend
    $env:PYTHONPATH='.'
    uv run python ..\\eval\\scripts\\ablation.py [--limit N] [--json]

三档都跑一遍:每档重新切块 → 子块 embed_passage(不加前缀) → 20 个 query embed_query(加前缀)
→ 余弦(点积,模型输出已 L2 归一化,不再归一化一遍)→ 命中子块折成父块(small-to-big)
→ 算 Recall@5 / Recall@10 / MRR@10,以及按 kind 的分项。

硬约束:
- **只读数据库**:只发 select notes,**一行都不写**。
- **不改 backend 源码**:三档参数通过临时替换 chunker 模块级常量注入(见 use_tier),
  作用域受限、退出无条件还原;脚本结尾断言已还原成生产值。
- **不新增依赖**:asyncpg / numpy 随 backend 依赖已在。
- 父块(小节)划分与档位无关:小节只由 heading / 表格 / 代码块边界决定,
  所以三档的候选父块集合完全相同,可比性来自这里。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

EVAL_DIR = Path(__file__).resolve().parents[1]  # eval/
BACKEND_DIR = EVAL_DIR.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.service.chunk import chunk as production_chunk  # noqa: E402
from app.service.chunk import chunker as chunker_mod  # noqa: E402
from app.service.embed import count_tokens, embed_passage, embed_query  # noqa: E402

QUERIES_CSV = EVAL_DIR / "queries" / "queries.csv"

# 取多少个「子块名次」再折成父块。父块候选数远大于 10,留足冗余才不会假性漏召回。
TOP_CHILD = 80
K_RECALL = (5, 10)
K_MRR = 10


@dataclass(frozen=True)
class Tier:
    """一档切块配置。只动子块尺度三项,不动 HARD_TOKEN_LIMIT(那是 512 截断的安全网)。"""

    name: str
    target: int
    min_chars: int
    max_chars: int


# 三档:MIN 取 TARGET 的 75%,MAX 取 TARGET 的 1.15 倍左右(200 档收紧一点,
# 400 档的 MAX 刻意压在 465:再往上就顶到 bge 512 token 上限,会触发静默截断)。
TIERS: tuple[Tier, ...] = (
    Tier("200", 200, 150, 260),
    Tier("300", 300, 250, 400),  # = 当前生产值(ADR 0004 §3)
    Tier("400", 400, 380, 480),
)


@dataclass
class ParentEntry:
    note_id: int
    note_title: str
    heading_path: str | None
    text: str


@dataclass
class ChildEntry:
    parent_idx: int
    text: str
    chars: int
    tokens: int
    # 所在小节(父块)的字符数:块长统计要按 ADR §6 口径把「小到装不下一个字块的小节」摘出去
    section_chars: int
    # 子块类型(code/text/table/...),来自 chunk.meta;前缀 A/B(eval/scripts/prefix_ablation.py)用。
    # 默认空串,向后兼容:既有 chunk 尺度消融不读它,数字不变。
    block_type: str = ""


@dataclass
class Corpus:
    tier: Tier
    parents: list[ParentEntry] = field(default_factory=list)
    children: list[ChildEntry] = field(default_factory=list)
    skipped_embed: int = 0


@dataclass
class Query:
    qid: str
    query: str
    kind: str
    difficulty: str
    expected_titles: list[str]
    expected_paths: list[str]

    @property
    def is_negative(self) -> bool:
        return self.kind == "negative"


# ---------------------------------------------------------------- 切块(三档)


@contextlib.contextmanager
def use_tier(tier: Tier):
    """临时把 chunker 的模块级尺度常量换成档位值,退出时无条件还原。

    chunker 的参数是模块级常量(`from .constants import TARGET_CHARS, ...`),没有配置注入点。
    这里做的是**进程内、作用域受限的临时替换**:不写文件、不永久改行为,
    退出时(含异常路径)把三个常量原样放回去,脚本末尾还会断言一次。
    """
    saved = (chunker_mod.TARGET_CHARS, chunker_mod.MIN_CHARS, chunker_mod.MAX_CHARS)
    chunker_mod.TARGET_CHARS = tier.target
    chunker_mod.MIN_CHARS = tier.min_chars
    chunker_mod.MAX_CHARS = tier.max_chars
    try:
        yield
    finally:
        chunker_mod.TARGET_CHARS, chunker_mod.MIN_CHARS, chunker_mod.MAX_CHARS = saved


_tok_cache: dict[str, int] = {}


def cached_count_tokens(text: str) -> int:
    """bge tokenizer 计数带缓存:同一段文本在「切块」和「统计」里会重复数一遍。"""
    n = _tok_cache.get(text)
    if n is None:
        n = count_tokens(text)
        _tok_cache[text] = n
    return n


def _norm_chars(s: str) -> int:
    return len(re.sub(r"\s+", "", s))


def build_corpus(notes: list[dict], tier: Tier) -> Corpus:
    """按某档参数切块,收集父块与「可向量化的子块」。"""
    corpus = Corpus(tier=tier)
    with use_tier(tier):
        for note in notes:
            result = production_chunk(note["doc"], token_counter=cached_count_tokens)
            for sec in result.sections:
                sec_chunks = [result.chunks[i] for i in sec.chunk_indices]
                parent = next((c for c in sec_chunks if c.is_parent), None)
                if parent is None:
                    # 没有父块文本的小节(理论上不该有):子块没有可折的目标,整节跳过
                    continue
                pidx = len(corpus.parents)
                sec_chars = _norm_chars(sec.text)
                corpus.parents.append(
                    ParentEntry(
                        note_id=note["id"],
                        note_title=note["title"],
                        heading_path=parent.heading_path,
                        text=parent.text,
                    )
                )
                for child in sec_chunks:
                    if child.is_parent:
                        continue
                    if child.meta.get("skipped_embed") or not child.text.strip():
                        corpus.skipped_embed += 1
                        continue
                    corpus.children.append(
                        ChildEntry(
                            parent_idx=pidx,
                            text=child.text,
                            chars=_norm_chars(child.text),
                            tokens=child.token_count,
                            section_chars=sec_chars,
                            block_type=child.meta.get("block_type") or "",
                        )
                    )
    return corpus


# ---------------------------------------------------------------- 检索与指标


def _norm_path(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def path_hit(actual: str | None, expected: str) -> bool:
    """heading_path 对得上:完全相等,或一方是另一方的严格前缀(面包屑相邻级)。"""
    a, e = _norm_path(actual), _norm_path(expected)
    if not a or not e:
        return False
    if a == e:
        return True
    return a.startswith(e + ">") or e.startswith(a + ">")


def rank_parents(scores: np.ndarray, children: list[ChildEntry]) -> list[int]:
    """子块按余弦降序 → 折成父块(同名次取最好),返回父块名次序列。"""
    order = np.argsort(-scores)[:TOP_CHILD]
    seen: set[int] = set()
    ranked: list[int] = []
    for ci in order:
        pidx = children[int(ci)].parent_idx
        if pidx in seen:
            continue
        seen.add(pidx)
        ranked.append(pidx)
    return ranked


def evaluate(
    corpus: Corpus, queries: list[Query], child_mat: np.ndarray, query_mat: np.ndarray
) -> dict:
    """一档的全部指标。命中口径见 eval/ablation.md §3。"""
    positives = [i for i, q in enumerate(queries) if not q.is_negative]
    negatives = [i for i, q in enumerate(queries) if q.is_negative]

    per_query: list[dict] = []
    for qi, q in enumerate(queries):
        scores = child_mat @ query_mat[qi]
        ranked = rank_parents(scores, corpus.children)
        hit_rank = None
        if not q.is_negative:
            for rank, pidx in enumerate(ranked, start=1):
                parent = corpus.parents[pidx]
                if parent.note_title not in q.expected_titles:
                    continue
                if any(path_hit(parent.heading_path, e) for e in q.expected_paths):
                    hit_rank = rank
                    break
        top1 = corpus.parents[ranked[0]] if ranked else None
        per_query.append(
            {
                "qid": q.qid,
                "kind": q.kind,
                "difficulty": q.difficulty,
                "hit_rank": hit_rank,
                "top1_score": float(np.max(scores)),
                "top1_note": top1.note_title if top1 else "",
                "top1_path": top1.heading_path if top1 else "",
                "ranked_notes": [corpus.parents[p].note_title for p in ranked[:10]],
            }
        )

    def _summary(indices: list[int]) -> dict:
        ranks = [per_query[i]["hit_rank"] for i in indices]
        n = len(indices)
        if n == 0:
            return {"n": 0}
        out: dict = {"n": n}
        for k in K_RECALL:
            out[f"recall@{k}"] = sum(1 for r in ranks if r is not None and r <= k) / n
        out[f"mrr@{K_MRR}"] = sum(1.0 / r for r in ranks if r is not None and r <= K_MRR) / n
        # 全量 MRR(不设截断):名字带 rank 上限会让答辩解释变复杂,这里额外给一个
        out["mrr"] = sum(1.0 / r for r in ranks if r is not None) / n
        return out

    overall = _summary(positives)
    by_kind = {}
    for kind in ("term", "paraphrase", "cross-section", "long", "short"):
        idx = [i for i in positives if per_query[i]["kind"] == kind]
        if idx:
            by_kind[kind] = _summary(idx)

    neg = [
        {
            "qid": per_query[i]["qid"],
            "top1_score": per_query[i]["top1_score"],
            "top1_note": per_query[i]["top1_note"],
        }
        for i in negatives
    ]
    pos_top1 = [per_query[i]["top1_score"] for i in positives]

    return {
        "tier": corpus.tier.name,
        "params": {
            "TARGET_CHARS": corpus.tier.target,
            "MIN_CHARS": corpus.tier.min_chars,
            "MAX_CHARS": corpus.tier.max_chars,
        },
        "corpus": corpus_stats(corpus),
        "overall": overall,
        "by_kind": by_kind,
        "negatives": neg,
        "pos_top1_score_mean": float(np.mean(pos_top1)) if pos_top1 else 0.0,
        "per_query": per_query,
    }


def corpus_stats(corpus: Corpus) -> dict:
    chars = sorted(c.chars for c in corpus.children)
    toks = sorted(c.tokens for c in corpus.children)
    n = len(chars)
    if n == 0:
        return {"parents": len(corpus.parents), "children": 0}

    def pct(seq: list[int], p: float) -> int:
        return seq[min(len(seq) - 1, int(round(p * (len(seq) - 1))))]

    lo, hi = corpus.tier.min_chars, corpus.tier.max_chars
    # ADR §6 口径:所在小节本身比 MIN_CHARS 还短的,它永远长不到目标长度,
    # 那是「小节粒度」问题不是「块尺度参数」问题,统计块长时单独摘出来看。
    big = sorted(c.chars for c in corpus.children if c.section_chars >= lo)
    return {
        "parents": len(corpus.parents),
        "children": n,
        "skipped_embed": corpus.skipped_embed,
        "chars_median": int(statistics.median(chars)),
        "chars_mean": round(statistics.fmean(chars), 1),
        "chars_p10": pct(chars, 0.10),
        "chars_p90": pct(chars, 0.90),
        "chars_min": chars[0],
        "chars_max": chars[-1],
        "bigsec_children": len(big),
        "bigsec_median": int(statistics.median(big)) if big else 0,
        "bigsec_in_band_ratio": (
            round(sum(1 for c in big if lo <= c <= hi) / len(big), 3) if big else 0.0
        ),
        "tokens_median": int(statistics.median(toks)),
        "tokens_max": toks[-1],
        "in_band_ratio": round(sum(1 for c in chars if lo <= c <= hi) / n, 3),
        # token > 480 会被 HARD_TOKEN_LIMIT 拦(合并时),> 512 是被 tokenizer 静默截断
        "over_limit_480": sum(1 for t in toks if t > 480),
        "truncated_512": sum(1 for t in toks if t > 512),
    }


# ---------------------------------------------------------------- 数据加载


async def load_notes(dsn: str, limit: int | None) -> list[dict]:
    """只读:从库里取 active 笔记的 block JSON(不写,不开事务)。"""
    import asyncpg  # 延迟导入:--help / 无 DB 时也能起

    conn = await asyncpg.connect(dsn)
    try:
        sql = (
            "select id, title, doc from notes "
            "where owner_id = 1 and status = 'active' order by id limit $1"
        )
        rows = await conn.fetch(sql, limit or 10000)
    finally:
        await conn.close()
    notes = []
    for row in rows:
        doc = row["doc"]
        if isinstance(doc, str):
            doc = json.loads(doc)
        notes.append({"id": row["id"], "title": row["title"], "doc": doc})
    return notes


def load_queries(path: Path) -> list[Query]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    queries = []
    for row in rows:
        titles = [t.strip() for t in (row["expected_note_title"] or "").split("||") if t.strip()]
        paths = [p.strip() for p in (row["expected_heading_path"] or "").split("||") if p.strip()]
        titles = [t for t in titles if t != "NA"]
        paths = [p for p in paths if p != "NA"]
        queries.append(
            Query(
                qid=row["qid"],
                query=row["query"],
                kind=row["kind"],
                difficulty=row["difficulty"],
                expected_titles=titles,
                expected_paths=paths,
            )
        )
    return queries


def check_expected_paths(notes: list[dict], queries: list[Query]) -> list[str]:
    """自检:每条正例的 expected_heading_path 必须能在库里的父块上找到。

    找不到说明题面写错了(或笔记没导进来),那这条 query 永远不可能命中 —— 必须先修。
    """
    corpus = build_corpus(notes, TIERS[1])  # 300 档 = 生产档,父块集合与档位无关
    actual = [(p.note_title, p.heading_path or "") for p in corpus.parents]
    problems = []
    for q in queries:
        if q.is_negative:
            continue
        for expected in q.expected_paths:
            if not any(
                title in q.expected_titles and path_hit(path, expected) for title, path in actual
            ):
                problems.append(f"{q.qid}: 找不到父块 heading_path = {expected}")
    return problems


# ---------------------------------------------------------------- 主流程


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def print_report(results: list[dict], queries: list[Query]) -> None:
    print("=" * 88)
    print("切块尺度消融(ADR 0004 §8)· 子块尺度 × 纯向量检索")
    print("=" * 88)

    print("\n[A] 三档 × 指标(命中口径:父块属于期望笔记 且 heading_path 与期望对得上)")
    print(
        f"{'档位':<7}{'父块':<6}{'子块':<6}{'中位(全量)':<12}{'中位(大节)':<12}"
        f"{'Recall@5':<11}{'Recall@10':<11}{'MRR':<8}"
    )
    print("-" * 84)
    for r in results:
        c, o = r["corpus"], r["overall"]
        print(
            f"{r['tier']:<7}{c['parents']:<6}{c['children']:<6}"
            f"{c['chars_median']:<12}{c['bigsec_median']:<12}"
            f"{_pct(o['recall@5']):<11}{_pct(o['recall@10']):<11}{o['mrr']:.3f}"
        )

    print("\n[B] 按 kind 分项(n = 该 kind 的正例条数)")
    kinds = ["term", "paraphrase", "cross-section", "long", "short"]
    head2 = f"{'kind':<16}" + "".join(f"{r['tier'] + '档':<26}" for r in results)
    print(head2)
    print(f"{'':<16}" + "".join(f"{'R@5':<9}{'R@10':<9}{'MRR':<8}" for _ in results))
    for kind in kinds:
        line = f"{kind:<16}"
        for r in results:
            b = r["by_kind"].get(kind)
            if not b:
                line += f"{'-':<26}"
            else:
                line += f"{_pct(b['recall@5']):<9}{_pct(b['recall@10']):<9}{b['mrr']:<8.3f}"
        print(line)
    print(f"{'(n)':<16}" + "".join(f"n={r['overall']['n']:<24}" for r in results))

    print("\n[C] 每档块长分布(字符数按去空白计,与切块器的 _norm_chars 同口径)")
    for r in results:
        c = r["corpus"]
        print(
            f"  {r['tier']}档 参数 TARGET={r['params']['TARGET_CHARS']} "
            f"MIN={r['params']['MIN_CHARS']} MAX={r['params']['MAX_CHARS']}"
        )
        print(
            f"       全量子块={c['children']} 中位={c['chars_median']} 均值={c['chars_mean']} "
            f"p10={c['chars_p10']} p90={c['chars_p90']} 区间内={_pct(c['in_band_ratio'])}"
        )
        print(
            f"       大节内子块(所在小节≥MIN)={c['bigsec_children']} 中位={c['bigsec_median']} "
            f"区间内={_pct(c['bigsec_in_band_ratio'])}"
        )
        print(
            f"       token 中位={c['tokens_median']} 最大={c['tokens_max']} "
            f"超480tok={c['over_limit_480']} 超512tok={c['truncated_512']} "
            f"跳过向量化={c['skipped_embed']}"
        )

    print("\n[D] 负例(纯向量检索无法返回空集,只看 top1 相似度是否明显偏低)")
    for r in results:
        negs = "；".join(
            f"{n['qid']} top1={n['top1_score']:.3f}({n['top1_note'][:16]})" for n in r["negatives"]
        )
        print(f"  {r['tier']}档 正例 top1 均值={r['pos_top1_score_mean']:.3f} | {negs}")

    print(f"\n[E] 逐条 query 命中名次(父块级;None = 前 {TOP_CHILD} 个子块里没折到)")
    head3 = f"{'qid':<6}{'kind':<16}" + "".join(f"{r['tier'] + '档':<8}" for r in results)
    print(head3)
    for i, q in enumerate(queries):
        if q.is_negative:
            continue
        line = f"{q.qid:<6}{q.kind:<16}"
        for r in results:
            rank = r["per_query"][i]["hit_rank"]
            line += f"{str(rank) if rank else '-':<8}"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="切块尺度消融:200/300/400 三档 × 向量检索指标")
    ap.add_argument(
        "--limit", type=int, default=None, help="只取前 N 篇笔记(快速自测用;交付数字必须全量)"
    )
    ap.add_argument("--json", action="store_true", help="额外把结构化结果打到 stdout(便于核对)")
    ap.add_argument("--queries", default=str(QUERIES_CSV), help="query 集 CSV 路径")
    args = ap.parse_args()

    queries = load_queries(Path(args.queries))
    print(f"query 集: {args.queries}({len(queries)} 条)")

    settings = get_settings()
    dsn = settings.database_url.replace("+asyncpg", "")
    notes = asyncio.run(load_notes(dsn, args.limit))
    print(f"语料: {len(notes)} 篇笔记(owner_id=1 / status=active)")
    if not notes:
        print("[error] 库里没有笔记,先按 HANDOFF 导入样本")
        return 1

    # --limit 是自测开关(只取前 N 篇),此时题面必然大量找不到,自检自动跳过
    problems = [] if args.limit else check_expected_paths(notes, queries)
    if problems:
        print("\n[error] 期望的 heading_path 在库里找不到,题面或语料有问题:")
        for p in problems:
            print(f"  - {p}")
        return 1

    pos = [q for q in queries if not q.is_negative]
    print(f"正例 {len(pos)} 条 / 负例 {len(queries) - len(pos)} 条")
    print("query 向量化(embed_query,带非对称前缀)……")
    query_mat = np.asarray([embed_query(q.query) for q in queries], dtype=np.float32)
    q_norm_median = float(np.median(np.linalg.norm(query_mat, axis=1)))
    print(f"  shape={query_mat.shape} 模长中位={q_norm_median:.4f}")

    results = []
    for tier in TIERS:
        print(
            f"\n>>> {tier.name} 档切块 "
            f"TARGET={tier.target} MIN={tier.min_chars} MAX={tier.max_chars}"
        )
        corpus = build_corpus(notes, tier)
        print(
            f"    父块 {len(corpus.parents)} / 子块 {len(corpus.children)}"
            f"(与档位无关的父块划分应三档一致)"
        )
        print(f"    子块向量化(embed_passage,不加前缀)…… {len(corpus.children)} 条")
        child_mat = np.asarray(embed_passage([c.text for c in corpus.children]), dtype=np.float32)
        print(f"    shape={child_mat.shape}")
        results.append(evaluate(corpus, queries, child_mat, query_mat))

    # 还原性断言:use_tier 退出后必须还是生产值
    assert (chunker_mod.TARGET_CHARS, chunker_mod.MIN_CHARS, chunker_mod.MAX_CHARS) == (
        300,
        250,
        400,
    ), "chunker 模块常量未还原!"

    print_report(results, queries)
    if args.json:
        print("\nJSON-BEGIN")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        print("JSON-END")
    return 0


if __name__ == "__main__":
    sys.exit(main())
