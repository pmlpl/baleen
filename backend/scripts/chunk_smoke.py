#!/usr/bin/env python
"""切块冒烟:对目录内每个文件 parse → chunk(真 bge token 计数),报硬指标。

    cd backend && uv run python scripts/chunk_smoke.py --samples ../eval/samples/_inbox

样本目录不存在直接 skip(exit 0)。不硬编码 eval/ 路径。
"""

import argparse
import os
import statistics
import sys
from pathlib import Path

# 从 baleen/.env.example 读 EMBEDDING_MODEL_PATH(脚本不经 conftest,要自己注入)
_env_example = Path(__file__).resolve().parents[2] / ".env.example"
if not os.environ.get("EMBEDDING_MODEL_PATH") and _env_example.exists():
    for _line in _env_example.read_text(encoding="utf-8").splitlines():
        if _line.startswith("EMBEDDING_MODEL_PATH="):
            os.environ["EMBEDDING_MODEL_PATH"] = _line.split("=", 1)[1].strip()
            break

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.chunk import chunk  # noqa: E402
from app.service.embed import count_tokens  # noqa: E402
from app.service.parse import parse_file  # noqa: E402

_PARSEABLE = {".md", ".markdown", ".docx", ".pdf", ".txt"}


def main() -> int:
    ap = argparse.ArgumentParser(description="切块冒烟:parse → chunk → 硬指标")
    ap.add_argument("--samples", required=True, help="样本目录路径")
    args = ap.parse_args()

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[skip] 样本目录不存在:{root}")
        return 0

    for f in sorted(root.iterdir()):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        if f.suffix.lower() not in _PARSEABLE:
            continue
        pr = parse_file(f)
        if pr.parse_status.value != "ok":
            print(f"{pr.parse_status.value:<20} {f.name[:40]:<42} (非 ok,跳过切块)")
            continue
        r = chunk(pr.blocks, token_counter=count_tokens)
        children = [c for c in r.chunks if not c.is_parent]
        toks = sorted(c.token_count for c in children)
        med = int(statistics.median(toks)) if toks else 0
        m = r.metrics
        print(
            f"ok   {f.name[:40]:<42} children={len(children):<4} median_tok={med:<4} "
            f"in_range={m['chunks_in_range_ratio']:<7} over={m['chunks_over_limit']} "
            f"trunc={m['chunks_truncated']} warns={m['warnings'][:1]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
