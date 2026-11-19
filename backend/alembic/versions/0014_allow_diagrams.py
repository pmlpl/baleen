"""app_settings 加结构图开关(allow_diagrams)

主人 2026-11-19 拍板:「全局默认开,但是否生图是要有依据的,不然我怕 AI
乱生图、生无意义的图」。

所以这个开关只控制**是否允许**生成结构图;真正的「该不该画」由
`app/service/rewrite/diagram_basis.py` 用代码算结构依据(并列清单 ≥5 项 /
流程阶段 ≥3 个),命中才注入 prompt,产出后还要校验节点有原文出处。
不开 = 完全不提画图(老行为)。

- NULL / true(默认) = 允许(仍受依据闸约束)
- false = 一律不生成

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("allow_diagrams", sa.Boolean, nullable=True, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "allow_diagrams")
