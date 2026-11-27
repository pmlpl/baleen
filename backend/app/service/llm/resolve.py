"""LLM 配置解析:每用户一行(app_settings),字段为 NULL 时按项的语义回退。

- base_url / model:非密钥,回退 .env 作为默认值(新用户开箱有个合理网关)
- api_key / tavily_key:**不回退**,必须由该用户自己配(用户已拍板:不继承别人的 key)
- charter:DB → 内置默认(charter.md 文件或 CHARTER 环境变量可覆盖,但 charter.md 已非常规配置)

每次调用前现读 → 设置页保存后**热生效**,无需重启。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.setting import AppSettings


async def _row(session: AsyncSession, owner_id: int) -> AppSettings | None:
    return await session.scalar(select(AppSettings).where(AppSettings.owner_id == owner_id))


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
    """返回 charter:该用户的 DB 配置优先,回退内置默认(charter.md 文件或 CHARTER 环境变量可覆盖)。"""
    row = await _row(session, owner_id)
    if row is not None and row.charter:
        return row.charter
    return get_settings().resolve_charter()


async def resolve_llm_budgets(
    session: AsyncSession, owner_id: int
) -> tuple[int | None, int | None]:
    """返回 (context_tokens, max_output_tokens),均为用户在设置页填的值。

    None = 用户没填,由调用方(organizer)用自己的兜底口径处理:
      - context_tokens=None → 不截断,整篇喂模型
      - max_output_tokens=None → 用 ORGANIZER_DEFAULT_OUTPUT_TOKENS
    不预设模型表:预算完全由用户填,没填走兜底。
    """
    row = await _row(session, owner_id)
    ctx = row.llm_context_tokens if (row is not None and row.llm_context_tokens) else None
    out = row.llm_max_output_tokens if (row is not None and row.llm_max_output_tokens) else None
    return ctx, out


async def resolve_bilibili_sessdata(session: AsyncSession, owner_id: int) -> str:
    """B 站 SESSDATA:该用户的扫码登录值优先,回退 .env 的 BILIBILI_SESSDATA。

    DB 值由扫码登录写入(bilibili_login.py),落库即热生效(前后端/worker 现读);
    .env 回退是纯兼容——历史上没人配过,且 .env 值需重启进程才可见(@lru_cache)。
    只给 Playwright 浏览器兜底用,绝不做 HTTP 重放(风控对 TLS 指纹打分,实测无效)。
    """
    row = await _row(session, owner_id)
    if row is not None and row.bilibili_sessdata:
        return row.bilibili_sessdata
    return get_settings().bilibili_sessdata


async def resolve_allow_diagrams(session: AsyncSession, owner_id: int) -> bool:
    """是否允许 organizer 生成结构图(主人 2026-11-19 拍板:全局默认开)。

    注意这只是「允许」——真正该不该画由 ``rewrite/diagram_basis.py`` 用代码
    算结构依据(并列清单 ≥5 项 / 流程阶段 ≥3 个),命中才注入 prompt。
    行不存在或字段为 NULL → True(默认开)。
    """
    row = await _row(session, owner_id)
    if row is None or row.allow_diagrams is None:
        return True
    return bool(row.allow_diagrams)


async def resolve_agent_enabled(session: AsyncSession, owner_id: int) -> bool:
    """问答 agent 开关(ADR 0010;主人 2026-11-20 验收拍板:**默认开**,迁移 0017)。

    行不存在或字段为 NULL → True(agent loop);false = 显式关回旧决策树
    (紧急开关/A-B 对照)。每次请求现读 → 设置页保存后热生效。
    agent 失败时回落决策树是代码层兜底,与这个开关无关(永远在)。
    """
    row = await _row(session, owner_id)
    if row is None or row.agent_enabled is None:
        return True
    return bool(row.agent_enabled)


async def resolve_nickname(session: AsyncSession, owner_id: int) -> str | None:
    """用户昵称(问卷事实化第一题,迁移 0020):问答 system prompt 注入,AI 用它称呼用户。

    行不存在或字段为 NULL → None(不称呼,按现状)。每次请求现读 → 问卷保存后热生效。
    """
    row = await _row(session, owner_id)
    if row is None:
        return None
    return row.nickname or None


__all__ = [
    "resolve_agent_enabled",
    "resolve_allow_diagrams",
    "resolve_bilibili_sessdata",
    "resolve_charter",
    "resolve_llm_budgets",
    "resolve_llm_config",
    "resolve_nickname",
    "resolve_tavily_key",
]
