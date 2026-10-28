"""检索:query → 向量/trgm 双路召回 → RRF 融合 → small-to-big(命中子块,返回父块)。"""

from .retriever import EmbedFunc, RetrievalHit, RetrievalResult, retrieve

__all__ = ["EmbedFunc", "RetrievalHit", "RetrievalResult", "retrieve"]
