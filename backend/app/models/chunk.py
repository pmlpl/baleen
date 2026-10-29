"""chunks —— 块(父子同表,ADR 0003 §2.4)。父块不 embed。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("(parent_id IS NULL) = is_parent", name="chunks_parent_is_parent_chk"),
        Index("chunks_note_ord_idx", "note_id", "ord"),
        Index(
            "chunks_parent_idx",
            "parent_id",
            postgresql_where=sa_text("parent_id IS NOT NULL"),
        ),
        Index("chunks_is_parent_idx", "is_parent"),
        Index(
            "chunks_text_trgm_idx",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chunks.id", ondelete="CASCADE")
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_parent: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, server_default=sa_text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    note = relationship("Note", back_populates="chunks")
    parent = relationship(
        "Chunk", remote_side="Chunk.id", back_populates="children", foreign_keys=[parent_id]
    )
    children = relationship("Chunk", back_populates="parent", foreign_keys=[parent_id])
    embeddings = relationship("Embedding", back_populates="chunk", cascade="all, delete-orphan")
