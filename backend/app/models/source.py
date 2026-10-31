"""sources —— 原始文件(ADR 0003 §2.1)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("owner_id", "sha256", name="sources_sha_uq"),
        CheckConstraint(
            "parse_status IN ('pending','ok','no_text_expected','unsupported_format','error',"
            "'pending_ocr')",
            name="sources_parse_status_chk",
        ),
        Index("sources_owner_created_idx", "owner_id", text("created_at DESC")),
        Index(
            "sources_parse_status_nonok_idx",
            "parse_status",
            postgresql_where=text("parse_status <> 'ok'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    ext: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(Text, server_default="'ok'", nullable=False)
    parse_meta: Mapped[dict] = mapped_column(
        JSONB, server_default=func.text("'{}'::jsonb"), nullable=False
    )
    doc: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    review_runs = relationship("ReviewRun", back_populates="source", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="source", cascade="all, delete-orphan")
