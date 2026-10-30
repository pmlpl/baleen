"""运行时模型配置 API(设置页),每用户一行。

字段为 NULL 时:base_url/model 回退 .env 默认值;api_key/Tavily key **不回退**
(每个用户必须自己配,不继承别人的 key)。热生效——每次 LLM 调用前现读。
api_key / tavily_api_key 只写不读:响应里只给「是否已设置 + 末 4 位」,永不回显全文。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentOwnerDep, SessionDep
from app.repo.settings import SettingsRepo
from app.service.llm.resolve import resolve_llm_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    llm_base_url: str
    llm_model: str
    api_key_set: bool  # False = 这个账号还没配 key,AI 功能不可用
    api_key_tail: str  # 末 4 位,便于确认是哪把 key
    tavily_set: bool
    tavily_tail: str  # 联网搜索(Tavily)key,同样只回末 4 位


class SettingsUpdate(BaseModel):
    """None/缺省 = 保持不变;空串 = 清空(回退默认);api_key 空串同样保持不变。"""

    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_api_key: str | None = Field(default=None, max_length=200)
    llm_model: str | None = Field(default=None, max_length=100)
    tavily_api_key: str | None = Field(default=None, max_length=200)


def _tail(secret: str | None) -> tuple[bool, str]:
    return bool(secret), secret[-4:] if secret else ""


@router.get("", response_model=SettingsOut)
async def read_settings(session: SessionDep, owner_id: CurrentOwnerDep) -> SettingsOut:
    base_url, api_key, model = await resolve_llm_config(session, owner_id)
    row = await SettingsRepo(session, owner_id).get()
    tavily = (row.tavily_api_key if row else None) or ""
    tavily_set, tavily_tail = _tail(tavily)
    api_set, api_tail = _tail(api_key)
    return SettingsOut(
        llm_base_url=base_url,
        llm_model=model,
        api_key_set=api_set,
        api_key_tail=api_tail,
        tavily_set=tavily_set,
        tavily_tail=tavily_tail,
    )


@router.put("", response_model=SettingsOut)
async def update_settings(
    payload: SettingsUpdate, session: SessionDep, owner_id: CurrentOwnerDep
) -> SettingsOut:
    await SettingsRepo(session, owner_id).save(
        llm_base_url=payload.llm_base_url,
        llm_api_key=payload.llm_api_key,
        llm_model=payload.llm_model,
        tavily_api_key=payload.tavily_api_key,
    )
    await session.commit()
    return await read_settings(session, owner_id)
