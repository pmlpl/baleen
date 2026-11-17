"""qa_digests 表:对话冻结分段摘要(Tier-1 长会话上下文管理)。

每段 digest 把若干早期 qa_messages 压成不可变摘要,作为 LLM 历史的压缩视角;
被覆盖的旧消息**不删**(qa_messages 表不动,前端仍显示完整对话原文),digest 只在
喂 LLM 时替代老消息重放。不可变分段保住上游前缀缓存命中(两次冻结之间前缀字节
不变 → 命中;冻结时只付一次 cache-write,之后长期 0.1× 读)。

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_digests",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, server_default="1", nullable=False),
        sa.Column(
            "session_id",
            sa.BigInteger,
            sa.ForeignKey("qa_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_msg_id", sa.BigInteger, nullable=False),
        sa.Column("end_msg_id", sa.BigInteger, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("qa_digests_session_idx", "qa_digests", ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("qa_digests_session_idx", table_name="qa_digests")
    op.drop_table("qa_digests")
