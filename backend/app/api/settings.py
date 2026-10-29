"""运行时模型配置 API(设置页)。

DB 单行(id=1)优先于 .env 缺省;热生效——每次 LLM 调用前现读,无需重启。
api_key 只写不读:响应里只给「是否已设置 + 末 4 位」,永不回显全文。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.api.library import OWNER_ID
from app.repo.settings import SettingsRepo
from app.service.llm.resolve import resolve_llm_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    llm_base_url: str
    llm_model: str
    api_key_set: bool
    api_key_tail: str  # 末 4 位,便于确认是哪把 key


class SettingsUpdate(BaseModel):
    """None/缺省 = 保持不变;空串 = 清空(回退 .env);api_key 空串同样保持不变。"""

    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_api_key: str | None = Field(default=None, max_length=200)
    llm_model: str | None = Field(default=None, max_length=100)


def _out(base_url: str, api_key: str, model: str) -> SettingsOut:
    return SettingsOut(
        llm_base_url=base_url,
        llm_model=model,
        api_key_set=bool(api_key),
        api_key_tail=api_key[-4:] if api_key else "",
    )


@router.get("", response_model=SettingsOut)
async def read_settings(session: SessionDep) -> SettingsOut:
    base_url, api_key, model = await resolve_llm_config(session)
    return _out(base_url, api_key, model)


@router.put("", response_model=SettingsOut)
async def update_settings(payload: SettingsUpdate, session: SessionDep) -> SettingsOut:
    await SettingsRepo(session, OWNER_ID).save(
        llm_base_url=payload.llm_base_url,
        llm_api_key=payload.llm_api_key,
        llm_model=payload.llm_model,
    )
    await session.commit()
    base_url, api_key, model = await resolve_llm_config(session)
    return _out(base_url, api_key, model)
