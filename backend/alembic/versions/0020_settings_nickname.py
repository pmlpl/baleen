"""app_settings.nickname:用户昵称(问卷事实化,2026-11-26)。

背景:旧问卷全是偏好题(「想收什么」「什么算好笔记」),用户没法回答——主人拍板
问卷改事实题(昵称/年龄段/性别/学历/当前状态/为什么用)。昵称是其中唯一要
**结构化消费**的:问答 system prompt 注入,AI 用它称呼用户(NULL = 不称呼)。
其余事实题只进 charter 生成的 LLM 输入,不落库。

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("nickname", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "nickname")
