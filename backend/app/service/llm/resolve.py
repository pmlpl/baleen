"""LLM 配置解析:设置页(DB 单行)优先,缺省回退 .env。

每次 LLM 调用前现读 → 设置页保存后**热生效**,无需重启。
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.setting import AppSettings


async def resolve_llm_config(session: AsyncSession) -> tuple[str, str, str]:
    """返回 (base_url, api_key, model):app_settings 覆盖 .env 对应项,字段为 NULL 回退。"""
    s = get_settings()
    row = await session.scalar(select(AppSettings).where(AppSettings.id == 1))
    if row is None:
        return s.llm_base_url, s.llm_api_key, s.llm_model
    return (
        row.llm_base_url or s.llm_base_url,
        row.llm_api_key or s.llm_api_key,
        row.llm_model or s.llm_model,
    )


async def resolve_tavily_key(session: AsyncSession) -> str | None:
    """Tavily key:app_settings 优先,回退环境变量 TAVILY_API_KEY。"""
    row = await session.scalar(select(AppSettings).where(AppSettings.id == 1))
    if row is not None and row.tavily_api_key:
        return row.tavily_api_key
    return os.getenv("TAVILY_API_KEY") or None


async def resolve_charter(session: AsyncSession) -> str:
    """返回 charter:app_settings.charter(问卷生成的)优先,回退 charter.md 文件 > env > 默认。

    与 resolve_llm_config / resolve_tavily_key 同口径:DB 单行优先,热生效。
    """
    row = await session.scalar(select(AppSettings).where(AppSettings.id == 1))
    if row is not None and row.charter:
        return row.charter
    return get_settings().resolve_charter()
