"""qa chat: qa_messages 加 llm_content（喂给 LLM 的原文，多轮前缀对齐用）

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30

用户消息存两份：content（原始问题，给人看）+ llm_content（<context> 检索片段+问题，给 LLM 重放）。
多轮对话按 llm_content 原样重放，会话内 prompt 前缀逐轮对齐，上游前缀缓存才能命中。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("qa_messages", sa.Column("llm_content", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("qa_messages", "llm_content")
