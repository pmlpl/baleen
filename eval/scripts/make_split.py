#!/usr/bin/env python3
"""按失败模式重新分层划分 dev/test（stratified-v3：粒度细化到 `(slot, verdict)`）。

**只改 `labels.csv` 的 `split` 列**；其余列（含全部 human_*）写回前逐字段校验。

为什么从 v2 升到 v3 —— v2 的粒度是「槽位」一级，同一个槽里「该收」和「该拒」两种失败模式
被混着分。最典型的是槽 7（扫描件/拍照笔记）：v2 的 test 里只有一张扫描发票（reject），
两张手写笔记（accept）全在 dev，于是「误拒率 0%」在 test 上是虚的 ——
一个见到扫描件就喊「拒」的傻瓜系统也能拿 0%。v3 把粒度改成 `(槽位, 判定)` 二元组。

规则（v3）：
1. **每个存在的 `(slot, human_verdict)` 组合在 test 里至少 1 条**（v3 的核心约束）；
2. sha256 相同的文件必须同 split（candidates.csv 的 dupe_of）；
3. `partial` 全进 test；
4. `slot 14` 进 test；
5. 至少 1 条 `topic_dialog` 进 test，否则误丢率分母为 0；
6. 总体接近 6:4 —— **但覆盖面优先于比例**，实际比例写进 split_meta.json；
7. 默认**锚定输入文件里已有的 test**，只做最小增量挪动（`--no-anchor-existing` 可关闭）。

用法：
    python eval/scripts/make_split.py --labels eval/labels.csv --dry-run
    python eval/scripts/make_split.py --labels eval/labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _common import write_csv

VERSION = "stratified-v3"
META_NAME = "split_meta.json"
# test 占比超过这个值就告警：覆盖面优先，但不能无上限地挤爆 dev
TEST_RATIO_CEILING = 0.55


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def read_dupes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return {r["rel_path"]: r["dupe_of"] for r in csv.DictReader(fh)
                if (r.get("dupe_of") or "").strip()}


def _pair(row: dict[str, str]) -> tuple[str, str]:
    return (row["slot"], row["human_verdict"])


def bundles_of(rows: list[dict[str, str]], dupes: dict[str, str]) -> list[dict[str, Any]]:
    """sha256 相同的行并成不可拆的 bundle（必须同 split），并记下它覆盖的 (槽位, 判定)。"""
    parent = {row["rel_path"]: row["rel_path"] for row in rows}
    by_rel = {row["rel_path"]: row for row in rows}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for rel, canonical in dupes.items():
        if rel in parent and canonical in parent:
            a, b = find(rel), find(canonical)
            if a != b:
                parent[b] = a

    groups: dict[str, list[str]] = {}
    for rel in parent:
        groups.setdefault(find(rel), []).append(rel)
    bundles = [{
        "rows": [by_rel[rel] for rel in sorted(rels)],
        "slots": {by_rel[rel]["slot"] for rel in rels},
        "pairs": {_pair(by_rel[rel]) for rel in rels},
        "rels": sorted(rels),
    } for rels in groups.values()]
    return sorted(bundles, key=lambda b: b["rels"][0].lower())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="分层重分配 labels.csv 的 split 列（slot×verdict 粒度）"
    )
    parser.add_argument("--labels", default="eval/labels.csv")
    parser.add_argument("--candidates", default="eval/candidates.csv", help="提供 dupe_of")
    parser.add_argument("--strategy", choices=("stratified",), default="stratified")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-ratio", type=float, default=0.4)
    parser.add_argument("--anchor-existing", dest="anchor_existing", action="store_true",
                        default=True, help="锚定输入文件里已有的 test，只做增量挪动（默认开）")
    parser.add_argument("--no-anchor-existing", dest="anchor_existing", action="store_false",
                        help="不锚定，完全按 seed 重新算（结果可能与旧 split 大不相同）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    labels_path = Path(args.labels)
    columns, rows = read_rows(labels_path)
    if not rows:
        print(f"[error] {labels_path} 没有数据", file=sys.stderr)
        return 2

    original_split = {row["rel_path"]: (row.get("split") or "").strip() for row in rows}

    dupes = read_dupes(Path(args.candidates))
    bundles = bundles_of(rows, dupes)
    random.Random(args.seed).shuffle(bundles)  # 只用于打破平局，保证可复现
    all_slots = {row["slot"] for row in rows}
    all_pairs = {_pair(row) for row in rows}
    overall = Counter(row["human_verdict"] for row in rows)
    total = sum(overall.values())
    target = max(1, round(len(rows) * args.test_ratio))

    def rows_of(picked: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [row for bundle in picked for row in bundle["rows"]]

    def deficit(bundle: dict[str, Any], counts: Counter) -> float:
        return sum(overall[row["human_verdict"]] / total * target - counts[row["human_verdict"]]
                   for row in bundle["rows"]) / len(bundle["rows"])

    # 规则 3/4：partial 与 slot 14 先无条件进 test
    chosen = [b for b in bundles
              if any(r["human_verdict"] == "partial" or r["slot"] == "14" for r in b["rows"])]
    # 规则 7：锚定已有的 test，避免为了重算而把冻结的划分洗一遍
    if args.anchor_existing:
        chosen += [b for b in bundles
                   if b not in chosen and any(original_split[r["rel_path"]] == "test"
                                              for r in b["rows"])]
    # 规则 5：topic_dialog 保底
    if not any((r.get("human_next_action") or "").strip() == "topic_dialog"
               for r in rows_of(chosen)):
        for bundle in bundles:
            if bundle not in chosen and any(
                    (r.get("human_next_action") or "").strip() == "topic_dialog"
                    for r in bundle["rows"]):
                chosen.append(bundle)
                break

    # 规则 1（v3 核心）：先补 (槽位, 判定) 组合覆盖，再按比例补到目标行数
    covered = {pair for bundle in chosen for pair in bundle["pairs"]}
    counts = Counter(row["human_verdict"] for row in rows_of(chosen))
    while True:
        uncovered = all_pairs - covered
        rest = [b for b in bundles if b not in chosen]
        if uncovered:
            scored = [(len(b["pairs"] & uncovered), deficit(b, counts), b) for b in rest
                      if b["pairs"] & uncovered]
            if not scored:
                print(f"[error] (槽位, 判定) 组合 {sorted(uncovered)} 没有可覆盖的样本",
                      file=sys.stderr)
                return 2
            gain, _, bundle = max(scored, key=lambda item: (item[0], item[1]))
            covered |= bundle["pairs"]
        elif len(rows_of(chosen)) < target and rest:
            bundle = max(rest, key=lambda b: deficit(b, counts))
        else:
            break
        chosen.append(bundle)
        counts.update(row["human_verdict"] for row in bundle["rows"])

    test_rels = {rel for bundle in chosen for rel in bundle["rels"]}
    splits = {row["rel_path"]: "test" if row["rel_path"] in test_rels else "dev" for row in rows}
    test_rows = [row for row in rows if splits[row["rel_path"]] == "test"]
    dev_rows = [row for row in rows if splits[row["rel_path"]] == "dev"]
    test_pairs = {_pair(row) for row in test_rows}

    missing_pairs = sorted(all_pairs - test_pairs,
                           key=lambda p: (int(p[0] or 0), p[1]))
    missing_slots = sorted(all_slots - {row["slot"] for row in test_rows},
                           key=lambda s: int(s or 0))
    problems = []
    if missing_pairs:
        problems.append(f"这些 (槽位, 判定) 组合在 test 里没有样本: {missing_pairs}")
    if missing_slots:
        problems.append(f"这些槽在 test 里没有样本: {missing_slots}")
    if not dev_rows:
        problems.append("dev 为空")
    problems += [f"重复对未同 split: {rel} / {canonical}" for rel, canonical in dupes.items()
                 if splits.get(rel) != splits.get(canonical)]
    problems += [f"partial 未进 test: {row['rel_path']}" for row in rows
                 if row["human_verdict"] == "partial" and splits[row["rel_path"]] != "test"]
    if problems:
        for problem in problems:
            print(f"[error] {problem}", file=sys.stderr)
        return 2

    def mix(items: list[dict[str, str]]) -> str:
        counter = Counter(row["human_verdict"] for row in items)
        return " ".join(f"{key}={counter[key]}" for key in ("accept", "partial", "reject")
                        if counter[key])

    actual_ratio = len(test_rows) / len(rows)
    print(f"[{args.strategy}] 样本 {len(rows)}，test {len(test_rows)} / dev {len(dev_rows)}"
          f"（占比 {actual_ratio:.1%}，目标 {args.test_ratio:.0%}，"
          f"锚定已有 test={args.anchor_existing}）")
    print(f"  test: {mix(test_rows)}")
    print(f"  dev : {mix(dev_rows)}")
    print(f"  槽位覆盖：{len(all_slots)} 个槽，test 覆盖 {len(all_slots) - len(missing_slots)} / "
          f"{len(all_slots)}")
    print(f"  (slot, verdict) 覆盖矩阵：{len(all_pairs)} 个组合，"
          f"test 覆盖 {len(all_pairs) - len(missing_pairs)} / {len(all_pairs)}")
    for slot, verdict in sorted(all_pairs, key=lambda p: (int(p[0] or 0), p[1])):
        n_test = sum(1 for r in test_rows if _pair(r) == (slot, verdict))
        n_dev = sum(1 for r in dev_rows if _pair(r) == (slot, verdict))
        flag = "" if n_test else "  ← 缺（应为 0）"
        print(f"    slot {slot:>2} · {verdict:<7} test={n_test} dev={n_dev}{flag}")
    if actual_ratio > TEST_RATIO_CEILING:
        print(f"  [warn] test 占比 {actual_ratio:.1%} 超过 {TEST_RATIO_CEILING:.0%} 上限 —— "
              f"覆盖面与比例不可兼得，需在交付里说明", file=sys.stderr)

    # 新旧 split 差异（相对输入文件的 split 列）
    to_test = sorted(rel for rel, old in original_split.items()
                     if old != "test" and splits[rel] == "test")
    to_dev = sorted(rel for rel, old in original_split.items()
                    if old == "test" and splits[rel] == "dev")
    print(f"  相对输入文件的 split 变化：dev→test {len(to_test)} 条 / test→dev {len(to_dev)} 条")
    for rel in to_test:
        print(f"    dev → test  {rel}")
    for rel in to_dev:
        print(f"    test → dev  {rel}  ← 不该出现，检查锚定逻辑")

    if args.dry_run:
        print("[dry-run] 未写 labels.csv")
        return 0

    for row in rows:
        row["split"] = splits[row["rel_path"]]
    write_csv(labels_path, columns, rows)
    labels_path.with_name(META_NAME).write_text(
        json.dumps(
            {
                "version": VERSION,
                "strategy": args.strategy,
                "seed": args.seed,
                "granularity": "slot_verdict",
                "test_ratio": args.test_ratio,
                "test_ratio_actual": round(actual_ratio, 4),
                "rows": len(rows),
                "test": len(test_rows),
                "dev": len(dev_rows),
                "slots": len(all_slots),
                "slot_verdict_pairs": len(all_pairs),
                "pairs_in_test": len(all_pairs) - len(missing_pairs),
                "anchored_to_existing": args.anchor_existing,
                "moved_dev_to_test": to_test,
                "moved_test_to_dev": to_dev,
            },
            ensure_ascii=False, indent=2,
        ) + "\n", encoding="utf-8")
    print(f"[ok] 已更新 {labels_path} 的 split 列；版本 {VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
