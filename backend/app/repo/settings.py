"""app_settings 的数据访问(每用户一行)。只 flush,commit 由调用方负责。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.setting import AppSettings
from app.repo.base import Repo


class SettingsRepo(Repo):
    async def get(self) -> AppSettings | None:
        return await self.session.scalar(
            select(AppSettings).where(AppSettings.owner_id == self.owner_id)
        )

    async def save(
        self,
        *,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        tavily_api_key: str | None = None,
        charter: str | None = None,
        nickname: str | None = None,
        age_band: str | None = None,
        gender: str | None = None,
        education: str | None = None,
        status: str | None = None,
        why: str | None = None,
        llm_context_tokens: int | None = None,
        llm_max_output_tokens: int | None = None,
        allow_diagrams: bool | None = None,
        agent_enabled: bool | None = None,
    ) -> AppSettings:
        """按传入字段更新;None = 保持不变,空串/0 = 清空(回退默认/兜底)。api_key 仅非空时更新。"""
        row = await self.get()
        if row is None:
            row = AppSettings(owner_id=self.owner_id)
            self.session.add(row)
        if llm_base_url is not None:
            row.llm_base_url = llm_base_url or None
        if llm_api_key:
            row.llm_api_key = llm_api_key
        if llm_model is not None:
            row.llm_model = llm_model or None
        if tavily_api_key is not None:
            row.tavily_api_key = tavily_api_key or None
        if charter is not None:
            row.charter = charter or None
        if nickname is not None:
            row.nickname = nickname or None
        # 画像页左栏五项事实(0021):同样 None=不动、空串=清空
        if age_band is not None:
            row.age_band = age_band or None
        if gender is not None:
            row.gender = gender or None
        if education is not None:
            row.education = education or None
        if status is not None:
            row.status = status or None
        if why is not None:
            row.why = why or None
        if llm_context_tokens is not None:
            # 0/负 = 清空(回退兜底:不截断全文喂);正数 = 用户填的上限
            row.llm_context_tokens = llm_context_tokens if llm_context_tokens > 0 else None
        if llm_max_output_tokens is not None:
            row.llm_max_output_tokens = llm_max_output_tokens if llm_max_output_tokens > 0 else None
        if allow_diagrams is not None:
            row.allow_diagrams = allow_diagrams
        if agent_enabled is not None:
            row.agent_enabled = agent_enabled
        await self.session.flush()
        return row
