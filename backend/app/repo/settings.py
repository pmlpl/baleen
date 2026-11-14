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
    ) -> AppSettings:
        """按传入字段更新;None = 保持不变,空串 = 清空(回退默认/文件)。api_key 仅非空时更新。"""
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
        await self.session.flush()
        return row
