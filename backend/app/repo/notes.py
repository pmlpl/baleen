"""notes 仓储:笔记真源 block JSON(ADR 0003 §2.3)。"""

from __future__ import annotations

from sqlalchemy import select, update

from app.models import Note

from .base import Repo


class NotesRepo(Repo):
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
