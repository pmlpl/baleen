"""app_settings 加 douyin_cookies(抖音视频导入需要的 cookie 字符串)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("douyin_cookies", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "douyin_cookies")
