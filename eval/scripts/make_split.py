#!/usr/bin/env python3
"""按失败模式槽位重新分层划分 dev/test（stratified-v2）。

**只改 `labels.csv` 的 `split` 列**；其余列（含全部 human_*）写回前逐字段校验。

规则（HANDOFF.md §2 · 问题 1）：
1. 每个 slot test 至少 1 条；2. sha256 相同的文件同 split（candidates.csv 的 dupe_of）；
3. partial 全进 test；4. slot 14 进 test；5. n≥2 的槽 test≥1 且 accept:reject 尽量接近；
6. 总体接近 6:4；7. 至少 1 条 topic_dialog 进 test，否则误丢率分母为 0。

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

VERSION = "stratified-v2"
META_NAME = "split_meta.json"

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

def bundles_of(rows: list[dict[str, str]], dupes: dict[str, str]) -> list[dict[str, Any]]:
    """sha256 相同的行并成不可拆的 bundle（必须同 split）。"""
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
        "rels": sorted(rels),
    } for rels in groups.values()]
    return sorted(bundles, key=lambda b: b["rels"][0].lower())

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分层重分配 labels.csv 的 split 列")
    parser.add_argument("--labels", default="eval/labels.csv")
    parser.add_argument("--candidates", default="eval/candidates.csv", help="提供 dupe_of")
    parser.add_argument("--strategy", choices=("stratified",), default="stratified")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-ratio", type=float, default=0.4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    labels_path = Path(args.labels)
    columns, rows = read_rows(labels_path)
    if not rows:
        print(f"[error] {labels_path} 没有数据", file=sys.stderr)
        return 2

    dupes = read_dupes(Path(args.candidates))
    bundles = bundles_of(rows, dupes)
    random.Random(args.seed).shuffle(bundles)  # 只用于打破平局，保证可复现
    all_slots = {row["slot"] for row in rows}
    overall = Counter(row["human_verdict"] for row in rows)
    total = sum(overall.values())
    target = max(1, round(len(rows) * args.test_ratio))

    def rows_of(picked: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [row for bundle in picked for row in bundle["rows"]]

    def deficit(bundle: dict[str, Any], counts: Counter) -> float:
        return sum(overall[row["human_verdict"]] / total * target - counts[row["human_verdict"]]
                   for row in bundle["rows"]) / len(bundle["rows"])

    chosen = [b for b in bundles
              if any(r["human_verdict"] == "partial" or r["slot"] == "14" for r in b["rows"])]
    if not any((r.get("human_next_action") or "").strip() == "topic_dialog"
               for r in rows_of(chosen)):
        for bundle in bundles:
            if bundle not in chosen and any(
                    (r.get("human_next_action") or "").strip() == "topic_dialog"
                    for r in bundle["rows"]):
                chosen.append(bundle)
                break

    covered = {slot for bundle in chosen for slot in bundle["slots"]}
    counts = Counter(row["human_verdict"] for row in rows_of(chosen))
    while True:  # 规则 1/5：先补齐槽位覆盖，再按比例补到目标行数
        uncovered = all_slots - covered
        rest = [b for b in bundles if b not in chosen]
        if uncovered:
            scored = [(len(b["slots"] & uncovered), deficit(b, counts), b) for b in rest
                      if b["slots"] & uncovered]
            if not scored:
                print(f"[error] 槽位 {sorted(uncovered)} 没有可覆盖的样本", file=sys.stderr)
                return 2
            gain, _, bundle = max(scored, key=lambda item: (item[0], item[1]))
            covered |= bundle["slots"]
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

    missing = sorted(all_slots - {row["slot"] for row in test_rows}, key=lambda s: int(s or 0))
    problems = []
    if missing:
        problems.append(f"这些槽在 test 里没有样本: {missing}")
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

    print(f"[{args.strategy}] 样本 {len(rows)}，test {len(test_rows)} / dev {len(dev_rows)}")
    print(f"  test: {mix(test_rows)}")
    print(f"  dev : {mix(dev_rows)}")
    for slot in sorted(all_slots, key=lambda s: int(s or 0)):
        print(f"  slot {slot:>2}: test={sum(1 for r in test_rows if r['slot'] == slot)} "
              f"dev={sum(1 for r in dev_rows if r['slot'] == slot)}")
    if args.dry_run:
        print("[dry-run] 未写 labels.csv")
        return 0

    for row in rows:
        row["split"] = splits[row["rel_path"]]
    write_csv(labels_path, columns, rows)
    labels_path.with_name(META_NAME).write_text(
        json.dumps({"version": VERSION, "strategy": args.strategy, "seed": args.seed,
                    "test_ratio": args.test_ratio, "rows": len(rows)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] 已更新 {labels_path} 的 split 列；版本 {VERSION}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
