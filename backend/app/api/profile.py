"""个人中心 API:账号身份 + 是否已填章程(仅此两项)。

**为什么只剩这么点(v38)**：主人 2026-11-25 铁律「不重叠功能和内容」。本端点原返回
四块聚合,其中三块是别处数据的第二份拷贝,前端 UI 删除后已无人消费:

- ``settings``(网关/模型/密钥掩码)→ 设置页 ``GET /api/settings`` 是唯一真源
  (原测试就断言 ``profile["settings"] == /api/settings 响应``,等于自证重复);
- ``stats``(笔记/来源/会话/消息计数)→ 前端使用统计区块已按主人意见删除;
- ``charter.preview``(章程前 300 字)→ 章程真源在 ``GET /api/charter``。

留下来的两项都是**本端点独有**的:
- ``user``(id/username/created_at)→ 顶栏只有用户名,注册时间无处可查;
- ``charter.has_charter`` → 前端据此决定按钮文案。用 ``GET /api/charter`` 也能判,
  但那要拉全文,只为取一个布尔值不值得。

密钥相关的东西现在**完全不出这个端点**,从根上消除了「同一份掩码两处返回」的风险。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentOwnerDep, CurrentUserDep, SessionDep
from app.repo.settings import SettingsRepo

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileUser(BaseModel):
    id: int
    username: str
    created_at: datetime  # 注册时间


class ProfileCharter(BaseModel):
    """只回「填没填」——完整章程走 GET /api/charter。"""

    has_charter: bool


class ProfileOut(BaseModel):
    user: ProfileUser
    charter: ProfileCharter


@router.get("", response_model=ProfileOut)
async def read_profile(
    user: CurrentUserDep, session: SessionDep, owner_id: CurrentOwnerDep
) -> ProfileOut:
    """账号身份 + 是否已填章程。统计与配置请走各自端点(不在这里做第二份)。"""
    row = await SettingsRepo(session, owner_id).get()
    charter_text = row.charter if row is not None else None
    return ProfileOut(
        user=ProfileUser(id=user.id, username=user.username, created_at=user.created_at),
        charter=ProfileCharter(has_charter=bool(charter_text)),
    )
