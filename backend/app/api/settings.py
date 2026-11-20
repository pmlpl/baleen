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
    # LLM 预算(per-user 自适应;None = 走兜底)
    llm_context_tokens: int | None  # None = 不截断,整篇喂模型
    llm_max_output_tokens: int | None  # None = 用 organizer 兜底(131072)
    # 结构图开关(默认 True)。True 也只是「允许」——真正该不该画由结构依据闸判定
    allow_diagrams: bool
    # 问答 agent 开关(ADR 0010,v32 起**默认开**;主人 2026-11-20 验收拍板)。
    # false = 显式关回旧决策树(紧急开关/A-B 对照)
    agent_enabled: bool


class SettingsUpdate(BaseModel):
    """None/缺省 = 保持不变;空串 = 清空(回退默认);api_key 空串同样保持不变。"""

    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_api_key: str | None = Field(default=None, max_length=200)
    llm_model: str | None = Field(default=None, max_length=100)
    tavily_api_key: str | None = Field(default=None, max_length=200)
    # LLM 预算:None/缺省 = 保持不变;0 = 清空(回退兜底)。用户填自己模型/厂商的上限。
    llm_context_tokens: int | None = Field(default=None, ge=0, le=2_000_000)
    llm_max_output_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    # 结构图开关:None = 保持不变
    allow_diagrams: bool | None = None
    # 问答 agent 开关:None = 保持不变;false = 显式关(与缺省同效)
    agent_enabled: bool | None = None


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
        llm_context_tokens=row.llm_context_tokens if row else None,
        llm_max_output_tokens=row.llm_max_output_tokens if row else None,
        allow_diagrams=(
            True if (row is None or row.allow_diagrams is None) else bool(row.allow_diagrams)
        ),
        agent_enabled=(
            True if (row is None or row.agent_enabled is None) else bool(row.agent_enabled)
        ),
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
        llm_context_tokens=payload.llm_context_tokens,
        llm_max_output_tokens=payload.llm_max_output_tokens,
        allow_diagrams=payload.allow_diagrams,
        agent_enabled=payload.agent_enabled,
    )
    await session.commit()
    return await read_settings(session, owner_id)
