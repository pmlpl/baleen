"""问答 agent 灰度开关 + 步骤留痕两列(ADR 0010)

背景: 问答链把「检索→相关性判定→[NO_KB]→联网/自答」硬编码决策树上翻为
agent loop(脑在 loop 里选工具,入口契约/出口校验/预算在马具)。

列:
- app_settings.agent_enabled —— 灰度开关,**默认关**(NULL/false = 走现有决策树,
  行为与 2026-11-20 之前的现状完全一致);开关热生效,设置页 API 读写。
  「脑不执缰」:预算/停不停永远在代码,这个开关只控制要不要把语义决策交给脑。
- qa_messages.trace —— agent 每步留痕(JSONB,授权审计):LLM 调用与工具调用
  各一条 {step, kind, tool, arg, result, latency_ms};老路径/老消息 = NULL。

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("agent_enabled", sa.Boolean, nullable=True, server_default=sa.text("false")),
    )
    op.add_column("qa_messages", sa.Column("trace", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("qa_messages", "trace")
    op.drop_column("app_settings", "agent_enabled")
