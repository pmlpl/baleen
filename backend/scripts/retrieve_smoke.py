"""检索冒烟:真实 bge + 真实开发库(线 K)。

用法:
    cd backend
    uv run python scripts/retrieve_smoke.py --query "闭包的作用域链" [--top-k 5]

需要:docker compose up -d(开发库已有真实导入数据)+ bge 模型(.env 的 EMBEDDING_MODEL_PATH)。
query 侧走 embed_query(带指令前缀的非对称接口);输出双路名次、RRF 分、父块 heading_path。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.retrieve import retrieve  # noqa: E402


def _short(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[:width] + "…"


def main() -> None:
    parser = argparse.ArgumentParser(description="混合检索冒烟:向量+trgm → RRF → 父块")
    parser.add_argument("--query", required=True, help="自然语言 query(必填)")
    parser.add_argument("--top-k", type=int, default=5, help="返回的父块数(默认 5)")
    args = parser.parse_args()

    try:
        result = asyncio.run(retrieve(args.query, top_k=args.top_k))
    except Exception as exc:  # noqa: BLE001 —— 冒烟脚本收敛为友好提示
        print(f"[error] {type(exc).__name__}: {exc}")
        print("需要 PostgreSQL(docker compose up -d)与 bge 模型(.env 的 EMBEDDING_MODEL_PATH)")
        sys.exit(1)

    print(f"query: {result.query}  (top_k={args.top_k})")
    if not result.hits:
        print("(无命中)")
        return
    for i, hit in enumerate(result.hits, 1):
        vec = f"vec#{hit.vector_rank}" if hit.vector_rank is not None else "vec--"
        lex = f"lex#{hit.lexical_rank}" if hit.lexical_rank is not None else "lex--"
        print(f"{i}. rrf={hit.rrf_score:.4f} [{vec} {lex}] {hit.heading_path or '(无标题路径)'}")
        print(f"   note: {hit.note_title}")
        print(f"   父块: {_short(hit.parent_text, 100)}")
        print(f"   子块: {_short(hit.child_text, 60)}")


if __name__ == "__main__":
    main()
