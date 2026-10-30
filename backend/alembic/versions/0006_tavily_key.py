"""app_settings 加 tavily_api_key(工具链:库外问题联网搜索兜底)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("tavily_api_key", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "tavily_api_key")
