"""qa_sessions.updated_at:最近活跃时间(会话列表排序 + 保留策略裁剪依据)。

会话列表从「按创建时间」改为「按最近活跃」排:旧会话发一轮新对话即上浮顶部,
仅打开/查看不刷新时间。同时是「每用户只保留最新 50 个会话」硬删裁剪的依据列。
存量行回填 updated_at = created_at(老会话的活跃时间 = 它的创建时间)。

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "qa_sessions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # 存量回填:老会话的「最近活跃」就是它的创建时间(add_column 的 now() 默认值对老行无意义)
    op.execute("UPDATE qa_sessions SET updated_at = created_at")
    op.create_index(
        "qa_sessions_owner_updated_idx", "qa_sessions", ["owner_id", sa.text("updated_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("qa_sessions_owner_updated_idx", table_name="qa_sessions")
    op.drop_column("qa_sessions", "updated_at")
