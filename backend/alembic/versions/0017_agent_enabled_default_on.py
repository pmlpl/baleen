"""问答 agent 开关默认开(主人 2026-11-20 验收拍板:「我们都已经是 agent 模式了,旧对话模式不要再是默认」)

背景: L1 交付时按任务书灰度口径默认关(0016);主人验收时改判——agent 就是问答模式,
不该让用户手动开。翻转: server_default false→true + 存量行全部置 true。

语义(翻转后): NULL/true = agent loop(默认); false = 显式关回旧决策树
(紧急开关/A-B 对照)。agent 失败回落决策树的代码层兜底与开关无关,永远在
(ADR 0010「最坏情况=现状」不拆)。

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("app_settings", "agent_enabled", server_default=sa.text("true"))
    op.execute("UPDATE app_settings SET agent_enabled = true WHERE agent_enabled IS NOT true")


def downgrade() -> None:
    op.alter_column("app_settings", "agent_enabled", server_default=sa.text("false"))
