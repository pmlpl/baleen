"""向量化闸(AGENTS §5 决策4):文本 → 1024 维单位向量。非对称检索。"""

from .embedder import (
    QUERY_PREFIX,
    count_tokens,
    embed_passage,
    embed_query,
    get_device,
    load_seconds,
)

# 落库到 embeddings.model 列的名字。该表主键是 (chunk_id, model),
# 一旦库里已有数据,这个字符串就再不能改(改了等于全都查不到向量)。
EMBEDDING_MODEL = "bge-large-zh-v1.5"

__all__ = [
    "EMBEDDING_MODEL",
    "QUERY_PREFIX",
    "count_tokens",
    "embed_passage",
    "embed_query",
    "get_device",
    "load_seconds",
]
