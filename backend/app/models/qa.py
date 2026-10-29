"""qa_sessions / qa_messages —— 聊天式问答的会话与消息(阶段2)。

一个 session 是一次「打开的会话」,title 取首问前 30 字;消息按 id 序回放。
assistant 消息把检索出处存 citations JSONB,前端渲染「出处」直接用,不再回查 chunks;
cache_hit/miss_tokens 记 DeepSeek 前缀缓存的原始计数,命中率由展示层算。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QaSession(Base):
    __tablename__ = "qa_sessions"
    __table_args__ = (Index("qa_sessions_owner_created_idx", "owner_id", text("created_at DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QaMessage(Base):
    __tablename__ = "qa_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="qa_messages_role_chk"),
        Index("qa_messages_session_idx", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # user 消息专用:真正喂给 LLM 的原文(含 <context> 检索片段)。多轮重放时用它而非 content,
    # 使会话内前缀逐轮对齐 → 上游前缀缓存命中(命中率从 ~0 提到 90%+)。assistant 消息为 NULL。
    llm_content: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(Text)
    cost_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
