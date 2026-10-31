"""sources.parse_status 加 pending 枚举:上传占位不再伪装成 error

- 新上传的 source 在 worker 处理前 parse_status 一直是 'error' 占位(语义不对:
  还没解析就「失败」,HANDOFF §1 真问题 2)。现在改用 'pending',CHECK 同步加枚举
- 存量数据不动:历史行都是终态(ok/error/...),不存在需要改写的 pending
- downgrade 直接还原旧约束;若降级时还有 pending 行会失败 —— pending 只存在于
  任务排队窗口,属瞬态,正常不会发生

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHK = "parse_status IN ('ok','no_text_expected','unsupported_format','error','pending_ocr')"
_NEW_CHK = (
    "parse_status IN ('pending','ok','no_text_expected','unsupported_format','error','pending_ocr')"
)


def upgrade() -> None:
    op.drop_constraint("sources_parse_status_chk", "sources", type_="check")
    op.create_check_constraint("sources_parse_status_chk", "sources", _NEW_CHK)


def downgrade() -> None:
    op.drop_constraint("sources_parse_status_chk", "sources", type_="check")
    op.create_check_constraint("sources_parse_status_chk", "sources", _OLD_CHK)
