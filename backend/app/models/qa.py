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
    __table_args__ = (
        Index("qa_sessions_owner_created_idx", "owner_id", text("created_at DESC")),
        Index("qa_sessions_owner_updated_idx", "owner_id", text("updated_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 最近活跃时间:落一条消息(user/assistant)即刷新,仅打开/查看不碰。
    # 会话列表按它排(最近活跃在前),也是「每用户只保留最新 50 个会话」的裁剪依据。
    updated_at: Mapped[datetime] = mapped_column(
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
    # agent 步骤留痕(ADR 0010 授权审计):每步 {step, kind, tool, arg, result, latency_ms}。
    # NULL = 老路径(决策树)的回答 / user 消息;agent 开关开的回答才有值。
    trace: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QaDigest(Base):
    """一段冻结的对话摘要:把若干早期消息压成不可变摘要,作为 LLM 历史的压缩视角。

    与 qa_messages 平行存在;被 digest 覆盖的旧消息**不删**(前端仍显示完整对话原文),
    digest 只在「喂 LLM 时」替代老消息重放 —— 既止住超长会话的静默丢失,又用不可变
    分段保住上游前缀缓存命中(两次冻结之间前缀字节不变 → 命中)。详见 ``digest.py``。
    """

    __tablename__ = "qa_digests"
    __table_args__ = (Index("qa_digests_session_idx", "session_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, server_default="1", nullable=False)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # 该摘要覆盖 [start_msg_id, end_msg_id] 闭区间的 qa_messages.id
    start_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
