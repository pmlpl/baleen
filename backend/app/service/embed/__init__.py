"""向量化闸(AGENTS §5 决策4):文本 → 1024 维单位向量。非对称检索。"""

from .embedder import (
    QUERY_PREFIX,
    count_tokens,
    embed_passage,
    embed_query,
    get_device,
    load_seconds,
)

__all__ = [
    "QUERY_PREFIX",
    "count_tokens",
    "embed_passage",
    "embed_query",
    "get_device",
    "load_seconds",
]
