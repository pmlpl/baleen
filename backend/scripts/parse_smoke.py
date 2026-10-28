#!/usr/bin/env python
"""G0 真实样本冒烟脚本(不进测试,不硬编码 eval 路径):

    cd backend && uv run python scripts/parse_smoke.py --samples ../eval/samples/_inbox

样本目录不存在时直接 skip(exit 0)。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.parse import parse_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="G0 解析冒烟:对目录内每个文件跑一遍并打印结论")
    ap.add_argument("--samples", required=True, help="样本目录路径")
    args = ap.parse_args()

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[skip] 样本目录不存在:{root}")
        return 0

    counts: dict[str, int] = {}
    for f in sorted(root.iterdir()):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        r = parse_file(f)
        counts[r.parse_status.value] = counts.get(r.parse_status.value, 0) + 1
        msg = (r.message or "")[:40]
        warn = ";".join(r.warnings)[:48]
        print(
            f"{r.parse_status.value:<20} {f.name[:42]:<44} "
            f"chars={r.meta.get('chars', 0):<6} tables={r.meta.get('tables', 0):<3} "
            f"images={r.meta.get('images', 0):<3} {msg} {warn}"
        )
    print("汇总:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
