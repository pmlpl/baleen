#!/usr/bin/env python3
"""baleen / eval · 阶段 3：指标计算器。

按 TASK.md D5 的顺序输出 10 节报告（判定 → 混淆矩阵 → 质量/全错 → 成本 → 非法组合）。
误拒率 / 误丢率置顶加粗：前者是「好笔记被扔了」，后者是「该再生的话题被扔了」。
labels 未标完时不崩：写出提示「还差 N 条未标注」，退出码 1。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

VERDICTS = ("accept", "partial", "reject")
# 质量档次假设：core > normal > reference > junk（reference 是查阅型资料）
QUALITY_RANK = {"core": 4, "normal": 3, "reference": 2, "junk": 1}
# D5 第 8 条：合法映射只有四条，其余组合算预测器自相矛盾
LEGAL_ACTIONS = {
    "accept": {"index"},
    "partial": {"extract_spans"},
    "reject": {"topic_dialog", "drop"},
}

def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))

def ratio(num: int, den: int) -> str:
    return "N/A（无该组样本）" if den == 0 else f"{num}/{den} = {num / den:.1%}"

def table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return (["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
            + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows])

def quantile(values: list[int], q: float) -> str:
    if not values:
        return "N/A"
    ordered = sorted(values)
    return f"{ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]} ms"

def write_blank_report(out_path: Path, split: str, scope: list[dict[str, str]],
                       blank: list[dict[str, str]]) -> None:
    lines = [f"# 评测报告 · split={split}（未计算）", "",
             f"**还差 {len(blank)} 条未标注，暂不计算指标。**", "",
             f"- 本 split 样本数：{len(scope)}",
             f"- 已标注：{len(scope) - len(blank)} / {len(scope)}", "",
             "## 未标注清单", ""]
    lines += [f"- `{row['rel_path']}`" for row in blank]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="计算评测指标，输出 report.md")
    parser.add_argument("--labels", default="eval/labels.csv")
    parser.add_argument("--preds", default="eval/preds.csv")
    parser.add_argument("--out", default="eval/report.md")
    parser.add_argument("--split", choices=("all", "dev", "test"), default="test")
    args = parser.parse_args(argv)

    labels_path, preds_path, out_path = Path(args.labels), Path(args.preds), Path(args.out)
    for path in (labels_path, preds_path):
        if not path.exists():
            print(f"[error] 文件不存在: {path}", file=sys.stderr)
            return 2

    labels = load_rows(labels_path)
    preds = {row["rel_path"]: row for row in load_rows(preds_path)}
    scope = [row for row in labels if args.split == "all" or row.get("split") == args.split]
    if not scope:
        print(f"[error] split={args.split} 下没有样本", file=sys.stderr)
        return 2

    blank = [row for row in scope
             if not row.get("human_verdict", "").strip()
             or not row.get("human_quality", "").strip()]
    if blank:
        write_blank_report(out_path, args.split, scope, blank)
        print(f"[warn] 还差 {len(blank)} 条未标注，未计算指标（已写出 {out_path}）",
              file=sys.stderr)
        return 1

    missing_pred = [row for row in scope if row["rel_path"] not in preds]
    rows = [(row, preds[row["rel_path"]]) for row in scope if row["rel_path"] in preds]

    def pf(pred: dict[str, str], key: str) -> str:
        return (pred.get(key) or "").strip()

    def count(pairs: list[tuple[dict[str, str], dict[str, str]]], cond: Any) -> int:
        return sum(1 for lab, pred in pairs if cond(lab, pred))

    # --- 指标 -------------------------------------------------------------
    good = [(lab, pred) for lab, pred in rows if lab["human_verdict"] in ("accept", "partial")]
    false_reject = count(good, lambda lab, pred: pf(pred, "pred_verdict") == "reject")
    topic = [(lab, pred) for lab, pred in rows
             if (lab.get("human_next_action") or "").strip() == "topic_dialog"]
    false_drop = count(topic, lambda lab, pred: pf(pred, "pred_action") == "drop")
    topic_hit = count(topic, lambda lab, pred: pf(pred, "pred_action") == "topic_dialog")
    correct = count(rows, lambda lab, pred: lab["human_verdict"] == pf(pred, "pred_verdict"))

    per_class, f1_by_class = [], {}
    for cls in VERDICTS:
        tp = count(rows, lambda lab, pred: lab["human_verdict"] == cls
                   and pf(pred, "pred_verdict") == cls)
        fp = count(rows, lambda lab, pred: lab["human_verdict"] != cls
                   and pf(pred, "pred_verdict") == cls)
        fn = count(rows, lambda lab, pred: lab["human_verdict"] == cls
                   and pf(pred, "pred_verdict") != cls)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_by_class[cls] = f1
        per_class.append([cls, tp + fn, f"{precision:.2f}", f"{recall:.2f}", f"{f1:.2f}"])
    # macro-F1 只在 human 或 pred 里实际出现过的类别上平均（与 sklearn 默认一致）
    seen = {lab["human_verdict"] for lab, _ in rows} | {pf(pred, "pred_verdict") for _, pred in rows}
    scored = [f1_by_class[cls] for cls in VERDICTS if cls in seen]
    macro_f1 = sum(scored) / len(scored) if scored else 0.0

    underrated = [(lab, pred) for lab, pred in rows
                  if QUALITY_RANK.get(lab["human_quality"], 0)
                  - QUALITY_RANK.get(pf(pred, "pred_quality"), 0) >= 2]
    all_wrong = [(lab, pred) for lab, pred in rows
                 if lab["human_verdict"] != pf(pred, "pred_verdict")
                 and (lab.get("human_next_action") or "").strip() != pf(pred, "pred_action")]
    illegal = [(lab, pred) for lab, pred in rows
               if pf(pred, "pred_action") not in LEGAL_ACTIONS.get(pf(pred, "pred_verdict"), set())]
    verdict_error = {lab["rel_path"] for lab, pred in rows
                     if lab["human_verdict"] != pf(pred, "pred_verdict")}
    unique_errors = verdict_error | {lab["rel_path"] for lab, _ in illegal}

    tokens = [int(float(pf(pred, "cost_tokens") or 0)) for _, pred in rows]
    latency = [int(float(pf(pred, "latency_ms") or 0)) for _, pred in rows]
    slot_counts = Counter((lab.get("slot") or "").strip() or "未填" for lab, _ in rows)
    split_counts = Counter((row.get("split") or "").strip() or "未填" for row in labels)
    meta_path = labels_path.with_name("split_meta.json")
    try:
        split_version = json.loads(meta_path.read_text(encoding="utf-8")).get("version", "未记录")
    except (OSError, ValueError):
        split_version = "未记录"

    # --- 报告 -------------------------------------------------------------
    lines = [f"# 评测报告 · split={args.split}", "", f"split 版本：{split_version}", "",
             f"**误拒率 {ratio(false_reject, len(good))}　|　"
             f"误丢率 {ratio(false_drop, len(topic))}**", "", "---", "",
             "## 1. 数据集概况", ""]
    lines += table(["项", "值"], [
        ["本 split 样本数", len(scope)],
        ["标注完成度", f"{len(scope)} / {len(scope)}"],
        ["参与计算的样本", len(rows)],
        ["缺失预测（preds 无此 rel_path）", len(missing_pred)],
        ["dev / test 分布（全库）", f"{split_counts.get('dev', 0)} / {split_counts.get('test', 0)}"],
    ])
    lines += ["", "槽位分布：", ""]
    lines += table(["slot", "样本数"], [[slot, n] for slot, n in sorted(
        slot_counts.items(), key=lambda kv: (kv[0] == "未填", int(kv[0]) if kv[0].isdigit() else 99))])
    lines += ["", "## 2. 🔴 误拒率", "",
              f"**误拒率 = {ratio(false_reject, len(good))}**（human ∈ {{accept, partial}} 中 "
              "pred = reject 的比例）—— 好笔记被扔了。", "",
              "## 3. 🔴 误丢率", "",
              f"**误丢率 = {ratio(false_drop, len(topic))}**（human_next_action = topic_dialog 中 "
              "pred_action = drop 的比例）—— 该再生的话题被扔了。", "",
              "## 4. 话题召回率", "",
              f"话题召回率 = {ratio(topic_hit, len(topic))}"
              "（human_next_action = topic_dialog 中 pred_action = topic_dialog 的比例）。", "",
              "## 5. 混淆矩阵", "",
              "行 = human，列 = pred（不含 pred 为空的行）。", ""]
    lines += table(["human \\ pred", *VERDICTS], [
        [human] + [count(rows, lambda lab, pred, h=human, p=pred_cls:  # noqa: E731
                          lab["human_verdict"] == h and pf(pred, "pred_verdict") == p)
                   for pred_cls in VERDICTS] for human in VERDICTS])
    lines += ["", "## 6. 判定准确率与各类 P / R / F1", "",
              f"- 判定准确率：{ratio(correct, len(rows))}",
              f"- macro-F1：{macro_f1:.2f}", ""]
    lines += table(["类别", "support", "precision", "recall", "F1"], per_class)
    lines += ["", "## 7. 质量分级被低估清单", "",
              "判据：`human_quality` 比 `pred_quality` 高两档及以上"
              "（档次 core > normal > reference > junk）。", ""]
    lines += table(["rel_path", "human_quality", "pred_quality"], [
        [lab["rel_path"], lab["human_quality"], pf(pred, "pred_quality")] for lab, pred in underrated
    ]) if underrated else ["无。"]
    lines += ["", "## 8. 全错样本清单（mining list）", "",
              "判据：判定与动作**都**判错。这些最该人工复看。", ""]
    lines += table(["rel_path", "human", "pred", "human_reason"], [
        [lab["rel_path"], f"{lab['human_verdict']} / {lab.get('human_next_action', '')}",
         f"{pf(pred, 'pred_verdict') or '（空）'} / {pf(pred, 'pred_action') or '（空）'}",
         (lab.get("human_reason") or "")[:60]] for lab, pred in all_wrong
    ]) if all_wrong else ["无。"]
    lines += ["", "## 9. 成本", ""]
    lines += table(["项", "值"], [
        ["总 token", sum(tokens)],
        ["均值 token / 文件", f"{sum(tokens) / len(tokens) if tokens else 0.0:.1f}"],
        ["P50 延迟", quantile(latency, 0.50)],
        ["P95 延迟", quantile(latency, 0.95)],
    ])
    lines += ["", "## 10. 🔴 非法组合清单", "",
              "合法映射只有 `accept→index`、`partial→extract_spans`、"
              "`reject→topic_dialog`、`reject→drop`；"
              "其余组合说明预测器自相矛盾，**计入错误数**。", "",
              f"**非法组合 = {len(illegal)} 条；判定错误 = {len(verdict_error)} 条；"
              f"去重后唯一错误 = {len(unique_errors)} 条（两类可能重叠）。**", ""]
    lines += table(["rel_path", "pred_verdict", "pred_action"], [
        [lab["rel_path"], pf(pred, "pred_verdict") or "（空）", pf(pred, "pred_action") or "（空）"]
        for lab, pred in illegal
    ]) if illegal else ["无。"]
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] {len(rows)} 条参与计算 → {out_path}（误拒率 {ratio(false_reject, len(good))}，"
          f"误丢率 {ratio(false_drop, len(topic))}，非法组合 {len(illegal)}）")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
