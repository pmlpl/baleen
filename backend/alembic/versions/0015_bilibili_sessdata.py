"""app_settings 加 B 站 SESSDATA 扫码登录三列(2026-11-19 深夜立项)

背景: B 站浏览器兜底(Playwright+SESSDATA 过风控挑战)需要登录态门票,但让用户
F12 手抄 SESSDATA 进 .env 从未发生过(11-19 财富自由 5583 两次导入死于此)。
改半自动:被风控时弹真浏览器扫码,登录后抓 SESSDATA 落库,数月免登录。

落盘拍板(主人 2026-11-19): app_settings DB 而非 .env ——
- .env 是 @lru_cache 进程级缓存,写完正在跑的后端/worker 都看不见,必须重启;
- baleen/.env 被 docker compose 共用,进程化写文件有搞坏启动配置的风险;
- SESSDATA 是用户的 B 站账号凭证,与 llm_api_key 同级,app_settings 每用户一行。

列:
- bilibili_sessdata —— 扫码登录抓到的 SESSDATA(NULL = 未配置,回退 .env 的 BILIBILI_SESSDATA)
- bilibili_sessdata_at —— 上次扫码时间(供 UI 显示「上次扫码」+ 排查过期)
- bilibili_uid —— 登录用户 DedeUserID(供 UI 显示已登录谁)

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("bilibili_sessdata", sa.Text(), nullable=True))
    op.add_column(
        "app_settings", sa.Column("bilibili_sessdata_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("app_settings", sa.Column("bilibili_uid", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "bilibili_uid")
    op.drop_column("app_settings", "bilibili_sessdata_at")
    op.drop_column("app_settings", "bilibili_sessdata")
