"""notes 仓储:笔记真源 block JSON(ADR 0003 §2.3)。"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update

from app.models import Note

from .base import Repo


class NotesRepo(Repo):
    async def count_active(self) -> int:
        """active 笔记总数(个人中心统计;口径与列表接口的 total 一致,archived 不数)。"""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Note)
                .where(Note.owner_id == self.owner_id, Note.status == "active")
            )
            or 0
        )

    async def get(self, note_id: int) -> Note | None:
        return await self.session.scalar(
            select(Note).where(Note.id == note_id, Note.owner_id == self.owner_id)
        )

    async def get_by_source(self, source_id: int) -> Note | None:
        return await self.session.scalar(
            select(Note).where(
                Note.owner_id == self.owner_id,
                Note.source_id == source_id,
                Note.status == "active",
            )
        )

    async def create(
        self,
        *,
        source_id: int | None,
        title: str,
        doc: dict,
        quality: str | None = None,
        lang: str | None = None,
    ) -> Note:
        note = Note(
            owner_id=self.owner_id,
            source_id=source_id,
            title=title,
            doc=doc,
            quality=quality,
            lang=lang,
            status="active",
        )
        self.session.add(note)
        await self.session.flush()
        return note

    async def update(
        self,
        note_id: int,
        *,
        title: str | None = None,
        doc: dict | None = None,
        quality: str | None = None,
        lang: str | None = None,
        status: str | None = None,
    ) -> None:
        values = {
            k: v
            for k, v in {
                "title": title,
                "doc": doc,
                "quality": quality,
                "lang": lang,
                "status": status,
            }.items()
            if v is not None
        }
        if values:
            await self.session.execute(
                update(Note)
                .where(Note.id == note_id, Note.owner_id == self.owner_id)
                .values(**values)
            )

    async def delete_active(self, note_id: int) -> bool:
        """硬删一篇 active 笔记,返回是否真删了(不存在/已归档 = False,404 口径同详情)。

        故意走 Core 级 delete 而不是 session.delete():Note.chunks 的 ORM 级 cascade
        会把全部子块 load 进内存再逐行删,大笔记又慢又占内存;Core delete 一条语句,
        chunks/embeddings 的级联(FK ondelete=CASCADE)由数据库自己完成。
        """
        result = await self.session.execute(
            delete(Note).where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "active",
            )
        )
        return (result.rowcount or 0) > 0
