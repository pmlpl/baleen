"""个人中心 API:一次聚合用户信息 + 使用统计 + 章程预览 + 配置掩码视图。

纯读聚合,复用现有数据访问(各表 Repo 的 count + resolve_llm_config + SettingsRepo),
不引入新表、不新增可写端点;编辑动作仍走原有端点(章程 §8 / 设置 §6)。
密钥掩码口径与设置页(settings.py)完全一致:只回「是否已设置 + 末 4 位」,永不回显全文。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentOwnerDep, CurrentUserDep, SessionDep
from app.repo.notes import NotesRepo
from app.repo.qa import QaRepo
from app.repo.settings import SettingsRepo
from app.repo.sources import SourcesRepo
from app.service.llm.resolve import resolve_llm_config

router = APIRouter(prefix="/api/profile", tags=["profile"])

# 章程在个人中心只做预览:超过这个字数截断加「…」,完整内容走 GET /api/charter
CHARTER_PREVIEW_CHARS = 300


class ProfileUser(BaseModel):
    id: int
    username: str
    created_at: datetime  # 注册时间


class ProfileStats(BaseModel):
    notes: int  # active 笔记数(口径同 GET /api/notes 的 total,archived 不数)
    sources: int  # 导入来源总数
    qa_sessions: int
    qa_messages: int


class ProfileCharter(BaseModel):
    has_charter: bool  # 与 GET /api/charter 的 has_charter 同口径
    preview: str | None  # 前 300 字(超长加「…」);完整章程走 GET /api/charter


class ProfileSettings(BaseModel):
    llm_base_url: str
    llm_model: str
    api_key_set: bool
    api_key_tail: str
    tavily_set: bool
    tavily_tail: str


class ProfileOut(BaseModel):
    user: ProfileUser
    stats: ProfileStats
    charter: ProfileCharter
    settings: ProfileSettings


def _tail(secret: str | None) -> tuple[bool, str]:
    """同 settings.py 的掩码口径:bool(已设置) + 末 4 位。"""
    return bool(secret), secret[-4:] if secret else ""


def _charter_view(text: str | None) -> ProfileCharter:
    if not text:
        return ProfileCharter(has_charter=False, preview=None)
    if len(text) <= CHARTER_PREVIEW_CHARS:
        return ProfileCharter(has_charter=True, preview=text)
    return ProfileCharter(has_charter=True, preview=text[:CHARTER_PREVIEW_CHARS] + "…")


@router.get("", response_model=ProfileOut)
async def read_profile(
    user: CurrentUserDep, session: SessionDep, owner_id: CurrentOwnerDep
) -> ProfileOut:
    """聚合个人中心一屏所需的数据;统计只数自己的(owner_id 作用域)。"""
    base_url, api_key, model = await resolve_llm_config(session, owner_id)
    row = await SettingsRepo(session, owner_id).get()
    tavily = (row.tavily_api_key if row else None) or ""
    api_set, api_tail = _tail(api_key)
    tavily_set, tavily_tail = _tail(tavily)
    return ProfileOut(
        user=ProfileUser(id=user.id, username=user.username, created_at=user.created_at),
        stats=ProfileStats(
            notes=await NotesRepo(session, owner_id).count_active(),
            sources=await SourcesRepo(session, owner_id).count(),
            qa_sessions=await QaRepo(session, owner_id).count_sessions(),
            qa_messages=await QaRepo(session, owner_id).count_messages(),
        ),
        charter=_charter_view(row.charter if row is not None else None),
        settings=ProfileSettings(
            llm_base_url=base_url,
            llm_model=model,
            api_key_set=api_set,
            api_key_tail=api_tail,
            tavily_set=tavily_set,
            tavily_tail=tavily_tail,
        ),
    )
