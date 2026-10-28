"""切块闸(ADR 0004):block JSON → 父块/子块。纯函数,不碰数据库与模型。"""

from .chunker import Chunk, ChunkResult, Section, chunk
from .constants import HARD_TOKEN_LIMIT, MAX_CHARS, MIN_CHARS, TARGET_CHARS

__all__ = [
    "Chunk",
    "ChunkResult",
    "Section",
    "chunk",
    "HARD_TOKEN_LIMIT",
    "MAX_CHARS",
    "MIN_CHARS",
    "TARGET_CHARS",
]
