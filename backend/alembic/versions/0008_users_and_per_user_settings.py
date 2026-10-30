"""多用户:新建 users 表、app_settings 改每用户一行、jobs 去重按 owner 隔离

- users:账号表;第一个注册的账号会显式拿到 id=1,继承单用户时期的存量数据
- app_settings 加 owner_id 并唯一约束:每个账号一行(含自己的 LLM 配置与章程)
  存量那行(id=1)自动归属 owner_id=1
- jobs_dedupe_uq 从 (dedupe_key) 改为 (owner_id, dedupe_key):
  否则两个用户上传同一个文件会互相去重,后一个用户的导入被静默跳过

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, autoincrement=True, primary_key=True),
        sa.Column("username", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # app_settings 改每用户一行;存量行自动落到 owner_id=1(单用户时期的那个主人)
    op.add_column(
        "app_settings",
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
    )
    op.create_unique_constraint("app_settings_owner_uq", "app_settings", ["owner_id"])
    # 存量行的 id 是手工写死的 1,序列没推进 → 新建行会撞主键。这里把序列对齐。
    op.execute(
        "SELECT setval('app_settings_id_seq', COALESCE((SELECT MAX(id) FROM app_settings), 1))"
    )

    # jobs 去重按用户隔离
    op.drop_constraint("jobs_dedupe_uq", "jobs", type_="unique")
    op.create_unique_constraint("jobs_dedupe_uq", "jobs", ["owner_id", "dedupe_key"])


def downgrade() -> None:
    op.drop_constraint("jobs_dedupe_uq", "jobs", type_="unique")
    op.create_unique_constraint("jobs_dedupe_uq", "jobs", ["dedupe_key"])

    op.drop_constraint("app_settings_owner_uq", "app_settings", type_="unique")
    op.drop_column("app_settings", "owner_id")

    op.drop_table("users")
