"""app_settings 加 charter(问卷生成的知识库章程,DB 优先于 charter.md 文件)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("charter", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "charter")
