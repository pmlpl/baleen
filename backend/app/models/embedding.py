"""embeddings —— 向量(独立表,ADR 0003 §2.5)。只有子块有行。

vec 用 pgvector 的 HALFVEC(1024)(bge-large-zh-v1.5 = 1024 维);主键 (chunk_id, model)
支持同维度多模型并存。换维度的模型要新建表(ADR §4.1),不原地覆盖。
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index(
            "embeddings_vec_hnsw",
            "vec",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"vec": "halfvec_cosine_ops"},
        ),
    )

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    vec: Mapped[list] = mapped_column(HALFVEC(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunk = relationship("Chunk", back_populates="embeddings")
