"""notes.archived_at:纸篓时间戳(软删除,ADR 0003 §3⑤ 落地)。

背景:ADR 0003 ⑤ 早就定了「软删除 archived + 检索过滤 active + 定时清理」,
`notes.status` 字段与所有查询的 `status='active'` 过滤也早已就位,但
`DELETE /api/notes/{id}` 一直走的是**硬删**,`archived` 在生产中从未出现过。
本迁移补上最后一环:

- `archived_at` 记录「移入纸篓的时刻」,30 天自动彻底删除的计时起点(主人 2026-11-25 拍板);
- 只对 `status='archived'` 的行有值,active 行为 NULL;
- 局部索引只覆盖 archived 行:每日清理只看这一小撮,不给正常查询增负担。

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    # 局部索引:清理任务只扫 status='archived' 的行(存量 archived = 0 行,无回填需求)
    op.execute(
        "CREATE INDEX notes_archived_at_idx ON notes (archived_at) WHERE status = 'archived'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS notes_archived_at_idx")
    op.drop_column("notes", "archived_at")
