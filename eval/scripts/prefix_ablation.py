#!/usr/bin/env python
"""heading 前缀 A/B(ADR 0004 §5):验证「前缀提升判别力」断言。

三变体 × 纯向量检索,300 档固定,与 chunk 尺度消融(eval/ablation.md)同口径:
  V1 = 生产现状:code 块带 `# {heading_path} ({lang})` 前缀;text/table 纯正文。
  V2 = 给 text/table 补 ADR §5 前缀(`{笔记标题} > {heading_path}`);code 不动。  ← 主比较
  V3 = 全量统一 ADR §5:所有块都 `{笔记标题} > {heading_path}\\n{正文}`(code 旧前缀被替换)。

硬约束(同 ablation.py):
- 只读数据库(只 select)。
- 不碰 chunker 源码:前缀注入在评测侧**后处理 child.text**,生产 embedding 与 chunker 零改动。
- 不新增依赖。
- V1 = identity 变换,必须复现生产 300 档数字(回归断言)。

复现:
    cd backend
    uv run python ..\\eval\\scripts\\prefix_ablation.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import numpy as np

EVAL_DIR = Path(__file__).resolve().parents[1]  # eval/
BACKEND_DIR = EVAL_DIR.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import ablation as abl  # noqa: E402  同目录;复用其切块/检索/指标实现

from app.core.config import get_settings  # noqa: E402
from app.service.embed import embed_passage, embed_query  # noqa: E402

QUERIES_CSV = EVAL_DIR / "queries" / "queries.csv"
TIER_300 = abl.TIERS[1]  # = 生产档(300/250/400)
VARIANTS = ("V1", "V2", "V3")
VARIANT_DESC = {
    "V1": "生产现状(code 带 # {hp} ({lang}) 前缀;text/table 纯正文)",
    "V2": "text/table 补 ADR §5 前缀;code 不动",
    "V3": "全量统一 ADR §5(所有块 {标题} > {hp};code 旧前缀替换)",
}


def canonical_prefix(note_title: str, heading_path: str | None) -> str:
    """ADR §5 拼接格式首行:{笔记标题} > {heading_path};无 heading_path 时只留标题。"""
    hp = (heading_path or "").strip()
    return f"{note_title} > {hp}" if hp else note_title


def transform_text(
    orig: str,
    block_type: str,
    note_title: str,
    heading_path: str | None,
    variant: str,
) -> str:
    """按变体改写子块 .text。V1 恒等(= 生产)。"""
    if variant == "V1":
        return orig
    pre = canonical_prefix(note_title, heading_path)
    if variant == "V2":
        # 只给 text/table 补前缀;code 维持生产前缀;其余类型不动。
        if block_type in ("text", "table"):
            return f"{pre}\n{orig}"
        return orig
    if variant == "V3":
        # 全量统一:code 的旧前缀(首行 `# ...`)剥掉再补 canonical;其余类型直接补。
        body = orig
        if block_type == "code":
            body = orig.split("\n", 1)[1] if "\n" in orig else orig
        return f"{pre}\n{body}"
    raise ValueError(f"未知变体: {variant}")


def run_variant(corpus, orig_meta, variant, query_mat, queries):
    """原地改写 child.text → 重新 embed → evaluate;跑完恢复原 text。"""
    for child, o in zip(corpus.children, orig_meta, strict=False):
        child.text = transform_text(
            o["text"], o["block_type"], o["note_title"], o["heading_path"], variant
        )
    child_mat = np.asarray(embed_passage([c.text for c in corpus.children]), dtype=np.float32)
    result = abl.evaluate(corpus, queries, child_mat, query_mat)
    result["variant"] = variant
    # 恢复原始 text(避免变体间互相污染)
    for child, o in zip(corpus.children, orig_meta, strict=False):
        child.text = o["text"]
    return result


def print_report(results, queries):
    print("=" * 84)
    print("heading 前缀 A/B(ADR 0004 §5)· 三变体 × 纯向量检索(300 档固定)")
    print("=" * 84)
    print("\n变体说明:")
    for v in VARIANTS:
        print(f"  {v} = {VARIANT_DESC[v]}")

    base = results[0]["overall"]
    print("\n[A] 三变体 × 指标(n = 18 正例;命中口径与 ablation.md §3 / §11 一致)")
    print(f"{'变体':<6}{'R@5':<10}{'R@10':<10}{'MRR':<9}{'ΔR@5 vs V1':<12}")
    print("-" * 55)
    for r in results:
        o = r["overall"]
        d = o["recall@5"] - base["recall@5"]
        sign = "+" if d >= 0 else ""
        print(
            f"{r['variant']:<6}{o['recall@5']*100:>5.1f}%   "
            f"{o['recall@10']*100:>5.1f}%   {o['mrr']:.3f}   {sign}{d*100:.1f}pt"
        )

    print("\n[B] 逐条命中名次(父块级;- = 前 80 子块里没折到)")
    head = f"{'qid':<6}{'kind':<16}" + "".join(f"{v:<8}" for v in VARIANTS)
    print(head)
    flips = []
    for i, q in enumerate(queries):
        if q.is_negative:
            continue
        ranks = [r["per_query"][i]["hit_rank"] for r in results]
        line = f"{q.qid:<6}{q.kind:<16}" + "".join(
            f"{str(rk) if rk else '-':<8}" for rk in ranks
        )
        print(line)
        if any(rk != ranks[0] for rk in ranks[1:]):
            flips.append((q.qid, ranks))

    if flips:
        print("\n[C] 名次有变化的 query(V2/V3 相对 V1):")
        for qid, ranks in flips:
            print(f"  {qid}: V1={ranks[0]}  V2={ranks[1]}  V3={ranks[2]}")
    else:
        print("\n[C] 名次有变化的 query: 无(三变体逐条名次完全一致)")

    print("\n[D] 负例 top1 相似度(看前缀是否改变近/远领域区分)")
    for r in results:
        negs = "；".join(f"{n['qid']}={n['top1_score']:.3f}" for n in r["negatives"])
        print(f"  {r['variant']}: 正例top1均值={r['pos_top1_score_mean']:.3f} | {negs}")


def main() -> int:
    ap = argparse.ArgumentParser(description="heading 前缀 A/B:V1/V2/V3 × 向量检索(300 档)")
    ap.add_argument("--queries", default=str(QUERIES_CSV), help="query 集 CSV 路径")
    args = ap.parse_args()

    queries = abl.load_queries(Path(args.queries))
    print(f"query 集: {args.queries}({len(queries)} 条)")

    settings = get_settings()
    dsn = settings.database_url.replace("+asyncpg", "")
    notes = asyncio.run(abl.load_notes(dsn, None))
    print(f"语料: {len(notes)} 篇笔记(owner_id=1 / status=active)")
    if not notes:
        print("[error] 库里没有笔记,先按 HANDOFF 导入样本")
        return 1

    # 300 档切块一次(= 生产档;父块划分与档位无关)
    corpus = abl.build_corpus(notes, TIER_300)
    print(f"300 档切块: 父块 {len(corpus.parents)} / 子块 {len(corpus.children)}")

    # 缓存每子块原始 text + block_type + 所属父块的 title/heading_path(变体变换用)
    orig_meta = []
    for c in corpus.children:
        p = corpus.parents[c.parent_idx]
        orig_meta.append(
            {
                "text": c.text,
                "block_type": c.block_type,
                "note_title": p.note_title,
                "heading_path": p.heading_path,
            }
        )
    bt = Counter(c.block_type for c in corpus.children)
    print(f"子块 block_type 分布: {dict(bt)}")

    pos = [q for q in queries if not q.is_negative]
    print(f"正例 {len(pos)} 条 / 负例 {len(queries) - len(pos)} 条")
    print("query 向量化(embed_query,带非对称前缀)……")
    query_mat = np.asarray([embed_query(q.query) for q in queries], dtype=np.float32)
    print(
        f"  shape={query_mat.shape} 模长中位="
        f"{float(np.median(np.linalg.norm(query_mat, axis=1))):.4f}"
    )

    results = []
    for v in VARIANTS:
        print(f"\n>>> {v} ({VARIANT_DESC[v]})")
        r = run_variant(corpus, orig_meta, v, query_mat, queries)
        results.append(r)
        o = r["overall"]
        print(
            f"    R@5={o['recall@5']*100:.1f}%  "
            f"R@10={o['recall@10']*100:.1f}%  MRR={o['mrr']:.3f}"
        )

    # ★ 回归断言:V1 = identity,必须复现生产 300 档 R@5=100%(ablation.md §11 宽松版)
    v1 = results[0]["overall"]
    assert abs(v1["recall@5"] - 1.0) < 1e-9, (
        f"V1 应复现生产 300 档 R@5=100%,实际 {v1['recall@5']*100:.1f}% —— 变换或复用有问题"
    )
    print(f"\n[回归] V1 R@5={v1['recall@5']*100:.1f}% = 生产 300 档 ✓(未改 chunker)")

    # 还原性断言:chunker 模块常量未被动过(同 ablation.py)
    assert (abl.chunker_mod.TARGET_CHARS, abl.chunker_mod.MIN_CHARS, abl.chunker_mod.MAX_CHARS) == (
        300,
        250,
        400,
    ), "chunker 模块常量未还原!"
    print("[还原] chunker 常量 = 300/250/400 ✓")

    print_report(results, queries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
