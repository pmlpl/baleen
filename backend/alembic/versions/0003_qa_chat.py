"""qa chat: qa_sessions + qa_messages(阶段2 聊天式问答)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29

挂 在 0002_data_model 之后。会话 title 取首问前 30 字;消息 citations 存 JSONB
(assistant 的检索出处);cache_hit/miss_tokens 记 DeepSeek 前缀缓存原始计数。
qa_messages.session_id 带 ondelete CASCADE —— 删会话连带删消息。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "qa_sessions_owner_created_idx", "qa_sessions", ["owner_id", sa.text("created_at DESC")]
    )
    op.create_table(
        "qa_messages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "session_id",
            sa.BigInteger,
            sa.ForeignKey("qa_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "citations", sa.dialects.postgresql.JSONB, nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column("cost_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cache_hit_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cache_miss_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("role IN ('user','assistant')", name="qa_messages_role_chk"),
    )
    op.create_index("qa_messages_session_idx", "qa_messages", ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("qa_messages_session_idx", table_name="qa_messages")
    op.drop_table("qa_messages")
    op.drop_index("qa_sessions_owner_created_idx", table_name="qa_sessions")
    op.drop_table("qa_sessions")
