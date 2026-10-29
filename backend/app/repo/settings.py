"""app_settings 的数据访问(单行,设置页用)。只 flush,commit 由调用方负责。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.setting import AppSettings
from app.repo.base import Repo


class SettingsRepo(Repo):
    async def get(self) -> AppSettings | None:
        return await self.session.scalar(select(AppSettings).where(AppSettings.id == 1))

    async def save(
        self,
        *,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
    ) -> AppSettings:
        """按传入字段更新;None = 保持不变,空串 = 清空(回退 .env)。api_key 仅非空时更新。"""
        row = await self.get()
        if row is None:
            row = AppSettings(id=1)
            self.session.add(row)
        if llm_base_url is not None:
            row.llm_base_url = llm_base_url or None
        if llm_api_key:
            row.llm_api_key = llm_api_key
        if llm_model is not None:
            row.llm_model = llm_model or None
        await self.session.flush()
        return row
