"""review_runs —— 判定记录(ADR 0003 §2.2)。一个闸门一行。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ReviewRun(Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        CheckConstraint("gate IN ('G0','G1','G2')", name="review_runs_gate_chk"),
        CheckConstraint(
            "verdict IN ('accept','partial','reject','skip')", name="review_runs_verdict_chk"
        ),
        CheckConstraint(
            "quality IS NULL OR quality IN ('core','normal','reference','junk')",
            name="review_runs_quality_chk",
        ),
        CheckConstraint(
            "action IS NULL OR action IN ('index','extract_spans','topic_dialog','drop')",
            name="review_runs_action_chk",
        ),
        Index("review_runs_source_gate_idx", "source_id", "gate"),
        Index("review_runs_created_idx", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    gate: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[str | None] = mapped_column(Text)
    note_type: Mapped[str | None] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    action: Mapped[str | None] = mapped_column(Text)
    reasons: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    cost_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source = relationship("Source", back_populates="review_runs")
