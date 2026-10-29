"""notes —— 笔记真源(ADR 0003 §2.3)。doc 是 block JSON,markdown 是缓存。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint("lang IS NULL OR lang IN ('zh','en','mixed')", name="notes_lang_chk"),
        CheckConstraint(
            "quality IS NULL OR quality IN ('core','normal','reference','junk')",
            name="notes_quality_chk",
        ),
        CheckConstraint("status IN ('active','archived')", name="notes_status_chk"),
        Index("notes_owner_status_idx", "owner_id", "status"),
        Index("notes_source_idx", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    doc: Mapped[dict] = mapped_column(JSONB, nullable=False)
    markdown: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), nullable=False
    )
    quality: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="'active'", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source = relationship("Source", back_populates="notes")
    chunks = relationship("Chunk", back_populates="note", cascade="all, delete-orphan")
