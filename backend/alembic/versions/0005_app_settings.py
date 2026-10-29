"""app_settings 单行表(设置页可改的模型配置,覆盖 .env 缺省)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("llm_base_url", sa.Text, nullable=True),
        sa.Column("llm_api_key", sa.Text, nullable=True),
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
