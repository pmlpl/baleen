"""notes.markdown 死列退役

- 该列自 ADR 0001 设计为「block JSON 的渲染缓存」,但导入/回填/编辑从未写过,实测全表 null
- GET /api/notes/{id} 的 markdown 字段从 2026-10-31 起由 notes.doc 现场序列化(v11),
  与该列无关;主人拍板「是死列就删,以后真有用再加」
- downgrade 原样加回(Text nullable),无数据可恢复(本来就全是 null)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("notes", "markdown")


def downgrade() -> None:
    op.add_column("notes", sa.Column("markdown", sa.Text(), nullable=True))
