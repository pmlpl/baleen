"""jobs —— 导入状态机(ADR 0003 §2.6)。dedupe_key 幂等;locked_by/locked_at 乐观锁。

⚠️ dedupe 是**按用户**的:唯一约束是 (owner_id, dedupe_key),不是单独的 dedupe_key。
   迁移 0008(多用户隔离)专门改过这一条——否则两个用户上传同一个文件会互相去重,
   后一个用户的导入被静默跳过。这里曾长期停在 0008 之前的老定义(只有 dedupe_key),
   模型与库不一致;修掉后 alembic check 才是绿的(2026-11-27)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # 按 owner 去重(迁移 0008):全局唯一会让 A、B 两账号上传同一文件时互相命中
        UniqueConstraint("owner_id", "dedupe_key", name="jobs_dedupe_uq"),
        CheckConstraint("kind IN ('import','reindex')", name="jobs_kind_chk"),
        CheckConstraint(
            "status IN ('queued','running','done','failed','dead')", name="jobs_status_chk"
        ),
        Index("jobs_status_locked_idx", "status", "locked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="'queued'", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
