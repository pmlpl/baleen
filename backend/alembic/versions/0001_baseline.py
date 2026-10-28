"""baseline(空):迁移链起点,后续建表迁移挂在这之后

Revision ID: 0001
Revises:
Create Date: 2026-10-28

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
