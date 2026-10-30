"""LLM 配置解析:每用户一行(app_settings),字段为 NULL 时按项的语义回退。

- base_url / model:非密钥,回退 .env 作为默认值(新用户开箱有个合理网关)
- api_key / tavily_key:**不回退**,必须由该用户自己配(用户已拍板:不继承别人的 key)
- charter:DB → charter.md 文件 → 内置默认

每次调用前现读 → 设置页保存后**热生效**,无需重启。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.setting import AppSettings


async def _row(session: AsyncSession, owner_id: int) -> AppSettings | None:
    return await session.scalar(
        select(AppSettings).where(AppSettings.owner_id == owner_id)
    )


async def resolve_llm_config(session: AsyncSession, owner_id: int) -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)。

    api_key 不回退 .env(每个用户必须自己配;空串表示没配,调用方应提示去设置页)。
    """
    s = get_settings()
    row = await _row(session, owner_id)
    if row is None:
        return s.llm_base_url, "", s.llm_model
    return (
        row.llm_base_url or s.llm_base_url,
        row.llm_api_key or "",
        row.llm_model or s.llm_model,
    )


async def resolve_tavily_key(session: AsyncSession, owner_id: int) -> str | None:
    """Tavily key:只看该用户自己的配置,不回退环境变量(不继承别人的 key)。"""
    row = await _row(session, owner_id)
    if row is not None and row.tavily_api_key:
        return row.tavily_api_key
    return None


async def resolve_charter(session: AsyncSession, owner_id: int) -> str:
    """返回 charter:该用户的 DB 配置优先,回退 charter.md 文件 > env > 默认。"""
    row = await _row(session, owner_id)
    if row is not None and row.charter:
        return row.charter
    return get_settings().resolve_charter()


__all__ = [
    "resolve_charter",
    "resolve_llm_config",
    "resolve_tavily_key",
]
