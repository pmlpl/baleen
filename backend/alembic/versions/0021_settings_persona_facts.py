"""app_settings 补存五项问卷事实(画像页左栏,2026-11-26)。

背景:迁移 0020 只把昵称落了库(问答要用它称呼用户),其余五个事实题
(年龄段/性别/学历/当前状态/为什么用)——当时只喂给 LLM 生成章程,生成完就丢了。
画像页左栏要「显示我当时填了什么」,没有数据源,所以补存。

为什么全部 nullable 不设默认值:老用户这一批数据本来就不存在,留 NULL 表示
「这题没答过」;画像页对 NULL 显示空位,不做假回填(编造用户没填过的答案是假的)。

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("age_band", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("gender", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("education", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("status", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("why", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "why")
    op.drop_column("app_settings", "status")
    op.drop_column("app_settings", "education")
    op.drop_column("app_settings", "gender")
    op.drop_column("app_settings", "age_band")
