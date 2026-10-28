#!/usr/bin/env python
"""G1 冒烟:对样本 parse → G1 判定,产出 a) 判定分布 b) 与 labels.csv 的混淆矩阵 c) 误拒率。

    cd backend && uv run python scripts/g1_smoke.py --samples ../eval/samples/_inbox

只读 eval/,一个字节都不改。缺目录就 skip。labels.csv 默认在样本目录的上两级(eval/)。
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.gate import judge  # noqa: E402
from app.service.parse import parse_file  # noqa: E402


def _find_labels(samples_root: Path, override: str | None) -> Path | None:
    if override:
        return Path(override)
    for p in (samples_root.parent, samples_root.parent.parent):
        cand = p / "labels.csv"
        if cand.exists():
            return cand
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="G1 冒烟:判定分布 + 混淆矩阵 + 误拒率")
    ap.add_argument("--samples", required=True, help="样本目录路径")
    ap.add_argument("--labels", default=None, help="labels.csv 路径(默认向上找两级)")
    args = ap.parse_args()

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[skip] 样本目录不存在:{root}")
        return 0

    labels_path = _find_labels(root, args.labels)
    human: dict[str, str] = {}
    if labels_path:
        with open(labels_path, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                human[row["filename"]] = row["human_verdict"]
    else:
        print("[warn] 未找到 labels.csv,只产出 G1 判定分布")

    dist: Counter[str] = Counter()
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    skipped: list[str] = []
    false_reject = 0
    human_ap = 0

    for f in sorted(root.iterdir()):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        pr = parse_file(f)
        if pr.parse_status.value != "ok":
            skipped.append(f"{f.name}({pr.parse_status.value})")
            continue  # 非 ok 不走 G1(扫描件/图片走 OCR 队列;unsupported/error 另处理)
        g = judge(pr)
        dist[g.verdict] += 1
        hv = human.get(f.name)
        if hv:
            matrix[hv][g.verdict] += 1
            if hv in ("accept", "partial"):
                human_ap += 1
                if g.verdict == "reject":
                    false_reject += 1

    print(f"a) G1 判定分布(共 {sum(dist.values())} 个 ok 文件): {dict(dist)}")
    print(f"   跳过(非 ok,不走 G1): {skipped}")
    print("b) 混淆矩阵(行=human_verdict,列=G1_verdict):")
    for hv in ("accept", "partial", "reject"):
        row = matrix.get(hv, Counter())
        print(f"   {hv:<8} accept={row.get('accept', 0):<3} reject={row.get('reject', 0):<3}")
    if human_ap:
        print(
            f"c) G1 误拒率(human accept/partial 中被 G1 reject): "
            f"{false_reject}/{human_ap} = {false_reject / human_ap:.1%}"
        )
    else:
        print("c) 无 human accept/partial 样本,算不出误拒率")
    return 0


if __name__ == "__main__":
    sys.exit(main())
