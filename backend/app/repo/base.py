"""Repo 薄封装骨架:所有业务查询的唯一入口(AGENTS.md §9、ADR 0003 §6.3)。

约定(后续任务在本类或其扩展里补具体方法时必须遵守):
- 构造时绑定 owner_id(单用户写死 1);凡带 owner_id 的表,查询都要 .where(owner_id == self.owner_id)
- notes 查询一律带 status='active'(软删除,ADR §3⑤);本骨架的 select_active_notes() 示范了这两个过滤
- 不许裸用 session 拼 SQL;session 由调用方注入(通常从 app.core.db.session_factory 取)

本文件只放骨架与共享查询片段,不放某个具体业务的完整查询。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note


class Repo:
    def __init__(self, session: AsyncSession, owner_id: int = 1) -> None:
        self.session = session
        self.owner_id = owner_id

    def select_active_notes(self) -> select:
        """骨架示例:owner 作用域 + 软删过滤。返回未执行的 Select,由调用方 await。"""
        return select(Note).where(
            Note.owner_id == self.owner_id,
            Note.status == "active",
        )
