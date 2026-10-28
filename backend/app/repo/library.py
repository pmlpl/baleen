"""library 仓储:列表 / 详情页面的**只读**查询(线 M)。

只读:本模块不提供任何写方法(仓库已冻结区,协调方只特批这一个只读查询文件)。
约定(与 base.py 一致,AGENTS §9 / ADR 0003 §6):
- 查询一律带 ``owner_id`` 作用域;notes 一律过滤 ``status='active'``(软删除,ADR §3⑤);
- 列表页要的两类派生数据都用**一次批量查询**取,不在循环里逐条 count(N+1):
  ``parent_chunk_counts``(每条笔记的父块数)、``note_ids_by_source``(source → note_id)。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, func, select

from app.models import Chunk, Note, Source

from .base import Repo

# ILIKE 的转义字符(默认就是反斜杠,这里显式传,免得依赖 PostgreSQL 的默认设置)
LIKE_ESCAPE = "\\"


def _like_pattern(text: str) -> str:
    """把用户输入包成 ``%...%`` 模式,并转义 ``%`` / ``_`` / ``\\``。

    不转义的话用户输入一个 ``%`` 就等于 ``匹配全部``,``_`` 会变成单字符通配。
    """
    escaped = (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


class LibraryRepo(Repo):
    """只读查询:供 GET /api/sources、GET /api/notes、GET /api/notes/{id} 使用。"""

    async def list_sources(
        self, *, parse_status: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[int, list[Source]]:
        """按 ``created_at DESC`` 分页;``parse_status`` 可省略。返回 (真实总数, 当前页)。

        总数走 ``count(*)`` 而不是 ``len(items)``:分页只返回一页,长度不等于总数。
        """
        base: Select = select(Source).where(Source.owner_id == self.owner_id)
        if parse_status is not None:
            base = base.where(Source.parse_status == parse_status)
        total = int(
            await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        rows = await self.session.scalars(
            base.order_by(Source.created_at.desc(), Source.id.desc()).limit(limit).offset(offset)
        )
        return total, list(rows)

    async def note_ids_by_source(self, source_ids: Sequence[int]) -> dict[int, int]:
        """一次批量取 source → note_id(列表页避免逐条查)。

        ``sources`` 与 ``notes`` 是 1:N(ADR §3②);列表只要一个代表,取 id 最小的那篇。
        """
        if not source_ids:
            return {}
        rows = await self.session.execute(
            select(Note.source_id, Note.id)
            .where(
                Note.owner_id == self.owner_id,
                Note.source_id.in_(list(source_ids)),
                Note.status == "active",
            )
            .order_by(Note.id)
        )
        mapping: dict[int, int] = {}
        for source_id, note_id in rows.all():
            mapping.setdefault(source_id, note_id)
        return mapping

    async def list_notes(
        self, *, q: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[int, list[Note]]:
        """按 ``created_at DESC`` 分页;``q`` 为标题 ILIKE 模糊匹配(不走向量)。"""
        base: Select = select(Note).where(Note.owner_id == self.owner_id, Note.status == "active")
        if q:
            base = base.where(Note.title.ilike(_like_pattern(q), escape=LIKE_ESCAPE))
        total = int(
            await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        rows = await self.session.scalars(
            base.order_by(Note.created_at.desc(), Note.id.desc()).limit(limit).offset(offset)
        )
        return total, list(rows)

    async def parent_chunk_counts(self, note_ids: Sequence[int]) -> dict[int, int]:
        """一次批量统计父块数(``chunk_count`` = section 数,不是子块数)。

        缺省的笔记不会出现在返回的 dict 里,调用方按 0 处理。
        """
        if not note_ids:
            return {}
        rows = await self.session.execute(
            select(Chunk.note_id, func.count())
            .where(Chunk.note_id.in_(list(note_ids)), Chunk.is_parent.is_(True))
            .group_by(Chunk.note_id)
        )
        return {note_id: int(count) for note_id, count in rows.all()}

    async def get_active_note(self, note_id: int) -> Note | None:
        """按 id 取未归档笔记;``archived`` 视为不存在(软删除,ADR §3⑤)。"""
        return await self.session.scalar(
            select(Note).where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "active",
            )
        )

    async def list_parent_chunks(self, note_id: int) -> list[Chunk]:
        """详情页只渲染父块:``is_parent=true``,按 ``ord ASC``。

        子块是检索的中间产物(检索接口的 ``child_text`` 才用它),这里不返回。
        """
        rows = await self.session.scalars(
            select(Chunk)
            .where(Chunk.note_id == note_id, Chunk.is_parent.is_(True))
            .order_by(Chunk.ord.asc())
        )
        return list(rows)
