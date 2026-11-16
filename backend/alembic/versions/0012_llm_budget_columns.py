"""app_settings 加 LLM 预算列(llm_context_tokens / llm_max_output_tokens)

per-user 自适应预算:用户在设置页填自己模型/厂商的上下文与输出上限。
- llm_context_tokens NULL = 不截断,整篇喂模型
- llm_max_output_tokens NULL = 用 organizer 兜底(ORGANIZER_DEFAULT_OUTPUT_TOKENS)
不预设模型表。

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("llm_context_tokens", sa.Integer, nullable=True))
    op.add_column(
        "app_settings", sa.Column("llm_max_output_tokens", sa.Integer, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_settings", "llm_max_output_tokens")
    op.drop_column("app_settings", "llm_context_tokens")
