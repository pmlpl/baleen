#!/usr/bin/env python
"""找「可转正为驳回样本」的候选（backlog #1 的补样本工具）。

## 背景：为什么需要这个工具

2026-11-19 分源 ok 率首跑发现一个结构性问题：

| 来源 | accept | partial | **reject** |
|---|---|---|---|
| 文档（老金标准）| 17 | 1 | **18** |
| 文档导入 | 3 | 0 | 0 |
| 视频转写 | 16 | 1 | **3** |
| 问答导出 | 12 | 0 | **0** |

**问答导出档一条 reject 都没有** —— 所以那一档的 0% 误拒率只证明了「不过度丢弃」，
**没证明「不过度接收」**。要补 reject 样本，但这类样本有个硬约束：

> 问答导出内容是我（AI）整理用户对话产生的，**天生质量不会太差**——真正该拒的
> 是「问一堆废话存成笔记」那种，只能在**真实使用**中产生，不能凭空造。
> 造出来的样本是假的，违反「评测集质量 = 抓没抓到真问题」的纪律。

所以本工具做的是**从现有候选池里筛**（而不是造）：
找出「机器判低价值 / 内容薄 / 营销味重」的候选，列给人复核，人工确认后才转正。

## 用法

    cd backend
    uv run python ../eval/scripts/find_reject_candidates.py            # 列表
    uv run python ../eval/scripts/find_reject_candidates.py --json     # 机器可读

## 判据（可调的启发式，不是最终裁决）

1. 机器 G2 判 reject / junk —— 最强信号
2. 正文很短（< 800 字符）—— 内容薄的典型
3. 含营销/无信息关键词（关注/点赞/加群/课程/优惠/一键三连/公众号）
4. `parse_status != ok` —— G0 都读不了

**本工具只出候选清单，不做裁决**——转正权在主人（评测标签纪律）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
BACKEND = EVAL_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND))

# 营销/无信息关键词（中文语料里的高频噪音）
MARKETING_KWS = (
    "关注公众号", "加群", "扫码", "优惠", "限时", "课程推荐", "购买链接",
    "一键三连", "点赞投币", "收藏", "订阅", "转发", "打赏", "私信",
    "薪资", "月薪", "年薪", "招聘", "内推",
)
SHORT_CHARS = 800


def _load_candidates() -> list[dict[str, str]]:
    path = EVAL_DIR / "inputs" / "candidates_log.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _load_labels_paths() -> set[str]:
    path = EVAL_DIR / "inputs" / "labels.csv"
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return {row["rel_path"] for row in csv.DictReader(fh)}


def _sample_text(archived_as: str) -> str:
    """读候选归档文件的前 4000 字符（够判内容薄不薄）。"""
    p = EVAL_DIR / "inputs" / "candidates" / archived_as
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return ""


def score_candidate(row: dict[str, str], text: str, already: bool) -> tuple[int, list[str]]:
    """给一条候选打分（分越高越像该拒的）。返回 (分数, 命中理由)。"""
    score = 0
    why: list[str] = []
    if already:
        why.append("已在金标准里")
        return -1, why

    g2 = (row.get("g2_verdict") or "").strip()
    q = (row.get("quality") or "").strip()
    if g2 == "reject":
        score += 5
        why.append("机器判 reject")
    if q == "junk":
        score += 4
        why.append("机器判 junk")

    ps = (row.get("parse_status") or "").strip()
    if ps and ps != "ok":
        score += 3
        why.append(f"parse_status={ps}")

    if text:
        if len(text) < SHORT_CHARS:
            score += 2
            why.append(f"内容短({len(text)}字)")
        hit_kw = [k for k in MARKETING_KWS if k in text]
        if hit_kw:
            score += 2 * min(len(hit_kw), 3)
            why.append(f"营销词: {'/'.join(hit_kw[:4])}")
    else:
        why.append("归档文件缺失（无法判内容）")
    return score, why


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="找可转正为驳回样本的候选（backlog #1）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--min-score", type=int, default=2, help="只列分数 ≥ 此值的（默认 2）")
    args = parser.parse_args(argv)

    candidates = _load_candidates()
    labeled = _load_labels_paths()

    rows = []
    for c in candidates:
        rel = f"_inbox/{c.get('archived_as', '')}"
        already = rel in labeled
        text = _sample_text(c.get("archived_as", ""))
        score, why = score_candidate(c, text, already)
        if score < args.min_score:
            continue
        rows.append({
            "archived_as": c.get("archived_as", ""),
            "filename": c.get("filename", ""),
            "g2_verdict": c.get("g2_verdict", ""),
            "quality": c.get("quality", ""),
            "parse_status": c.get("parse_status", ""),
            "score": score,
            "why": why,
            "already_labeled": already,
        })

    rows.sort(key=lambda r: -r["score"])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    print("可转正为驳回样本的候选（backlog #1）")
    print("=" * 78)
    if not rows:
        print("没有达到阈值的候选。")
        print("")
        print("说明：这说明**现有候选池里没有明显该拒的样本**。")
        print("问答导出的 reject 样本只能在真实使用中产生（问一堆废话存成笔记），")
        print("不能凭空造——造出来是假样本。等主人日常用出这种内容再补。")
        return 0

    for r in rows:
        print(f"[{r['score']:>2}分] {r['filename'][:52]}")
        print(f"      机器: {r['g2_verdict'] or '—'}/{r['quality'] or '—'}  "
              f"parse: {r['parse_status'] or '—'}")
        print(f"      理由: {'; '.join(r['why'])}")
        print("")

    print("=" * 78)
    print(f"共 {len(rows)} 条候选。")
    print("")
    print("⚠️ **本工具只出候选，不做裁决。** 转正权在主人（评测标签纪律）：")
    print("   看内容确认「确实该拒」后，按以下步骤转正——")
    print("   1. 把文件从 eval/inputs/candidates/ 挪到 eval/inputs/samples/_inbox/")
    print("   2. 在 eval/inputs/labels.csv 追加一行（human_verdict=reject）")
    print("   3. 重跑 metrics.py 看分源数字变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
