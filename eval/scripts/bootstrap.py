#!/usr/bin/env python3
"""baleen / eval · bootstrap 置信区间(D 路线:统计严谨性)。

动机(主人 2026-11-02):n=35 样本 / 20 query 上只报点估计,100% 的 R@5 置信区间宽得
经不起论文答辩追问。本脚本用 **有放回重采样 + 百分位法** 给所有已报告的指标补 95% CI——
n 小不可怕,可怕的是只报点估计;样本数 + CI 才是诚实的报告。

两种模式:
1. G1/G2 判定指标(默认):labels.csv + preds.csv,对配对样本重采样;
   误拒率 / 误丢率 / 话题召回率 / 判定准确率 / macro-F1。
   --split test / dev 各跑一遍 = 交叉验证视角(test 18 / dev 17,不共享样本)。
2. 检索指标:ablation.py --json 的 per_query 输出,对 positives 的 hit_rank 重采样;
   Recall@5 / Recall@10 / MRR@10 / MRR。

方法学(论文可直接引用):B=2000 次有放回重采样,seed=42 可复现,95% 百分位区间;
分母为 0 的轮次不计入(单独报有效轮数)。

用法:
    python eval/scripts/bootstrap.py --split test
    python eval/scripts/bootstrap.py --split dev
    python eval/scripts/bootstrap.py --per-query eval/ablation_perquery.json
    python eval/scripts/bootstrap.py --split test --out eval/ci_test.md
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path

N_BOOT = 2000
SEED = 42
CI_LEVEL = 0.95

VERDICTS = ("accept", "partial", "reject")
QUALITY_RANK = {"core": 4, "normal": 3, "reference": 2, "junk": 1}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def percentile_ci(samples: list[float]) -> tuple[float, float]:
    ordered = sorted(samples)
    lo_q = (1 - CI_LEVEL) / 2 * 100
    hi_q = (1 + CI_LEVEL) / 2 * 100
    def pct(q: float) -> float:
        idx = min(len(ordered) - 1, max(0, int(round(q / 100 * (len(ordered) - 1)))))
        return ordered[idx]
    return pct(lo_q), pct(hi_q)


def bootstrap(
    n: int,
    stat: Callable[[list[int]], float],
    *,
    rng: random.Random,
    n_boot: int = N_BOOT,
) -> tuple[float, float, float, int]:
    """对 0..n-1 索引有放回重采样;stat 返回 NaN(分母 0)的轮次不计。返回 (点估计, lo, hi, 有效轮)。"""
    point = stat(list(range(n)))
    samples: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        value = stat(idx)
        if value == value:  # NaN 检测
            samples.append(value)
    lo, hi = percentile_ci(samples)
    return point, lo, hi, len(samples)


def _fmt(point: float, lo: float, hi: float, valid: int) -> list[str]:
    return [
        f"{point:.1%}",
        f"[{lo:.1%}, {hi:.1%}]",
        str(valid),
    ]


# ---------------- G1/G2 判定指标 ----------------


def g12_metrics(labels_path: Path, preds_path: Path, split: str) -> list[list[str]]:
    labels = load_rows(labels_path)
    preds = {row["rel_path"]: row for row in load_rows(preds_path)}
    scope = [row for row in labels if split == "all" or row.get("split") == split]
    rows = [
        (lab, preds[lab["rel_path"]])
        for lab in scope
        if lab["rel_path"] in preds
        and (lab.get("human_verdict") or "").strip()
        and (lab.get("human_quality") or "").strip()
    ]
    if not rows:
        raise SystemExit(f"[error] split={split} 下没有可计算的配对样本")

    def pf(pred: dict[str, str], key: str) -> str:
        return (pred.get(key) or "").strip()

    def make_stat(name: str) -> Callable[[list[int]], float]:
        def stat(idx: list[int]) -> float:
            sub = [rows[i] for i in idx]
            good = [(lab, pred) for lab, pred in sub
                    if lab["human_verdict"] in ("accept", "partial")]
            topic = [(lab, pred) for lab, pred in sub
                     if (lab.get("human_next_action") or "").strip() == "topic_dialog"]
            if name == "false_reject":
                if not good:
                    return float("nan")
                return sum(1 for lab, pred in good if pf(pred, "pred_verdict") == "reject") / len(good)
            if name == "false_drop":
                if not topic:
                    return float("nan")
                return sum(1 for lab, pred in topic if pf(pred, "pred_action") == "drop") / len(topic)
            if name == "topic_recall":
                if not topic:
                    return float("nan")
                return sum(1 for lab, pred in topic if pf(pred, "pred_action") == "topic_dialog") / len(topic)
            if name == "accuracy":
                return sum(1 for lab, pred in sub
                           if lab["human_verdict"] == pf(pred, "pred_verdict")) / len(sub)
            if name == "macro_f1":
                seen = ({lab["human_verdict"] for lab, _ in sub}
                        | {pf(pred, "pred_verdict") for _, pred in sub})
                f1s = []
                for cls in VERDICTS:
                    if cls not in seen:
                        continue
                    tp = sum(1 for lab, pred in sub
                             if lab["human_verdict"] == cls and pf(pred, "pred_verdict") == cls)
                    fp = sum(1 for lab, pred in sub
                             if lab["human_verdict"] != cls and pf(pred, "pred_verdict") == cls)
                    fn = sum(1 for lab, pred in sub
                             if lab["human_verdict"] == cls and pf(pred, "pred_verdict") != cls)
                    precision = tp / (tp + fp) if tp + fp else 0.0
                    recall = tp / (tp + fn) if tp + fn else 0.0
                    f1s.append(2 * precision * recall / (precision + recall)
                               if precision + recall else 0.0)
                return sum(f1s) / len(f1s) if f1s else float("nan")
            raise ValueError(name)  # noqa: TRY003 —— 内部调用,枚举受控

        return stat

    names = ["false_reject", "false_drop", "topic_recall", "accuracy", "macro_f1"]
    titles = {
        "false_reject": "误拒率(越低越好)",
        "false_drop": "误丢率(越低越好)",
        "topic_recall": "话题召回率",
        "accuracy": "判定准确率",
        "macro_f1": "macro-F1",
    }
    header = [f"指标", "点估计", f"{int(CI_LEVEL * 100)}% 置信区间", "有效轮数"]
    out: list[list[str]] = []
    for name in names:
        rng = random.Random(SEED)  # 每个指标同一 seed,轮次对齐(可复现)
        point, lo, hi, valid = bootstrap(len(rows), make_stat(name), rng=rng)
        out.append([titles[name], *_fmt(point, lo, hi, valid)])
    return [header, *out]


# ---------------- 检索指标 ----------------


def retrieval_metrics(per_query_path: Path) -> list[list[str]]:
    """per_query JSON → 检索指标 CI。

    ⚠️ per_query 字典里没有 is_negative 字段,负例靠 ``kind == "negative"`` 识别
    (q19/q20);它们的 hit_rank 恒为 None,**必须从分母里排除**,否则 R@5 被负例稀释。
    正例 hit_rank=None = 「没命中」,必须计入分母(这就是召回率的含义)。
    """
    data = json.loads(per_query_path.read_text(encoding="utf-8"))
    per_query = data["per_query"] if isinstance(data, dict) else data
    ranks = [
        q.get("hit_rank")
        for q in per_query
        if (q.get("kind") or "") != "negative"
    ]
    n = len(ranks)
    if n == 0:
        raise SystemExit("[error] per_query 里没有正例 query")

    def make_stat(name: str) -> Callable[[list[int]], float]:
        def stat(idx: list[int]) -> float:
            sub = [ranks[i] for i in idx]
            if name.startswith("recall@"):
                k = int(name.split("@")[1])
                return sum(1 for r in sub if r is not None and r <= k) / n
            if name == "mrr@10":
                return sum(1.0 / r for r in sub if r is not None and r <= 10) / n
            return sum(1.0 / r for r in sub if r is not None) / n

        return stat

    specs = [
        ("recall@5", "Recall@5"),
        ("recall@10", "Recall@10"),
        ("mrr@10", "MRR@10"),
        ("mrr", "MRR(全量)"),
    ]
    header = [f"指标(n={n} 条正例 query)", "点估计", f"{int(CI_LEVEL * 100)}% 置信区间", "有效轮数"]
    out: list[list[str]] = []
    for name, title in specs:
        rng = random.Random(SEED)  # 每个指标同一 seed,轮次对齐
        point, lo, hi, valid = bootstrap(n, make_stat(name), rng=rng)
        out.append([title, *_fmt(point, lo, hi, valid)])
    return [header, *out]


# ---------------- 输出 ----------------


def render(title: str, intro: str, table: list[list[str]]) -> str:
    head, *body = table
    lines = [f"# {title}", "", intro, "",
             f"方法:B={N_BOOT} 次有放回重采样,seed={SEED} 可复现,"
             f"{int(CI_LEVEL * 100)}% 百分位区间;分母为 0 的轮次不计(见有效轮数)。", "",
             "| " + " | ".join(head) + " |",
             "|" + "|".join(["---"] * len(head)) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in body]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bootstrap 置信区间(评测指标)")
    parser.add_argument("--labels", default="eval/labels.csv")
    parser.add_argument("--preds", default="eval/preds.csv")
    parser.add_argument("--per-query", default=None,
                        help="ablation.py --json 的输出;给了就算检索指标 CI")
    parser.add_argument("--split", choices=("test", "dev", "all"), default="test")
    parser.add_argument("--out", default=None, help="写 markdown 文件(不写则打 stdout)")
    args = parser.parse_args(argv)

    blocks: list[str] = []
    if args.per_query:
        table = retrieval_metrics(Path(args.per_query))
        blocks.append(render(
            "检索指标 bootstrap 置信区间",
            "数据:`ablation.py --json` 的 per_query 命中名次(真链路,300 档切块语料)。",
            table,
        ))
    else:
        table = g12_metrics(Path(args.labels), Path(args.preds), args.split)
        blocks.append(render(
            f"G1/G2 判定指标 bootstrap 置信区间(split={args.split})",
            "数据:labels.csv(人工金标准)× preds.csv(线 J 预测)的配对样本。",
            table,
        ))

    text = "\n\n".join(blocks)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[ok] → {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
