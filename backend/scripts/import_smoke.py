#!/usr/bin/env python
"""导入状态机冒烟:文件 → 去重 → G0 → G1 →（G2）→ note → 切块 → 向量化 → 落库。

    cd backend && uv run python scripts/import_smoke.py --file <path> --no-llm
    cd backend && uv run python scripts/import_smoke.py --file <path> --no-llm --dry-run

- ``--no-llm``:跳过 G2,用 G1 结论产 note(没有 LLM key 也能跑通全流程)。
- ``--dry-run``:只走到 G1,不落库、不写 blob。
- 同一文件再跑一次 → sha256 去重命中,直接返回已有 source。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.imports import run_import  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="导入状态机冒烟")
    ap.add_argument("--file", required=True, help="待导入文件路径")
    ap.add_argument("--no-llm", action="store_true", help="跳过 G2(无 key 也能跑)")
    ap.add_argument("--dry-run", action="store_true", help="只走到 G1,不落库")
    args = ap.parse_args()

    try:
        r = run_import(args.file, no_llm=args.no_llm, dry_run=args.dry_run)
    except Exception as exc:  # 冒烟脚本:收敛成一行错误,不打 traceback
        print(f"[error] {type(exc).__name__}: {exc}")
        return 1

    print(f"文件: {args.file}")
    print(
        f"模式: {'dry-run(不落库)' if r.dry_run else '落库'}{' / 跳过 G2' if args.no_llm else ''}"
    )
    if r.deduped:
        print("去重: 命中已有 sha256 —— 未重复处理、未重复调用 G2")
    print("各阶段耗时(ms): " + "  ".join(f"{name}={ms}" for name, ms in r.stages))
    print(
        f"parse_status={r.parse_status}  g1={r.g1_verdict}  g2={r.g2_verdict}  "
        f"quality={r.quality}  action={r.action}"
    )
    print(f"source_id={r.source_id}  note_id={r.note_id}")
    print(
        f"chunks: total={r.chunks_total} (父块 {r.chunks_parents} / 子块 {r.chunks_children})  "
        f"embeddings={r.embeddings}"
    )
    if r.message:
        print(f"message: {r.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
