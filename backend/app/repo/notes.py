"""notes 仓储:笔记真源 block JSON(ADR 0003 §2.3)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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

        ⚠️ 自 0019 起业务路径**不再直接调它**:DELETE /api/notes/{id} 已改软删,
        真删只由「彻底删除」端点与 30 天清理调 ``purge_archived`` / ``purge_expired_archived``。
        本方法保留给测试与内部工具用。
        """
        result = await self.session.execute(
            delete(Note).where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "active",
            )
        )
        return (result.rowcount or 0) > 0

    # ---------------- 纸篓:软删除(ADR 0003 §3⑤ 落地) ----------------

    async def archive_active(self, note_id: int) -> bool:
        """把一篇 active 笔记移入纸篓(软删除)。

        只改 status + archived_at,不动 chunks/embeddings/blob —— 所以放回是零成本的,
        而所有查询本来就过滤 ``status='active'``,移入之后立刻从列表与检索里消失。
        返回是否真的移入了(不存在/已在纸篓 = False → 调用方按 404 处理)。
        """
        result = await self.session.execute(
            update(Note)
            .where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "active",
            )
            .values(status="archived", archived_at=func.now())
        )
        return (result.rowcount or 0) > 0

    async def restore_archived(self, note_id: int) -> bool:
        """把一篇纸篓里的笔记放回书架。返回是否真的放回了(不在纸篓 = False)。"""
        result = await self.session.execute(
            update(Note)
            .where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "archived",
            )
            .values(status="active", archived_at=None)
        )
        return (result.rowcount or 0) > 0

    async def purge_archived(self, note_id: int) -> bool:
        """彻底删除一篇**纸篓里**的笔记(真删,级联清 chunks/embeddings)。

        只认 archived:避免「彻底删除」端点被用来绕过软删直接毁一篇 active 笔记。
        """
        result = await self.session.execute(
            delete(Note).where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "archived",
            )
        )
        return (result.rowcount or 0) > 0

    async def list_archived(self, *, limit: int, offset: int) -> tuple[int, list[Note]]:
        """纸篓清单:按移入时间倒序(最近丢的在上),返回 (总数, 当前页)。"""
        base = select(Note).where(Note.owner_id == self.owner_id, Note.status == "archived")
        total = int(
            await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        rows = await self.session.scalars(
            base.order_by(Note.archived_at.desc(), Note.id.desc()).limit(limit).offset(offset)
        )
        return total, list(rows)

    async def get_archived(self, note_id: int) -> Note | None:
        """取一篇纸篓里的笔记(彻底删除端点要先确认它真在纸篓里)。"""
        return await self.session.scalar(
            select(Note).where(
                Note.id == note_id,
                Note.owner_id == self.owner_id,
                Note.status == "archived",
            )
        )


async def purge_expired_archived(session: AsyncSession, cutoff: datetime) -> list[tuple[int, int]]:
    """**系统级**清理:跨 owner 真删所有 ``archived_at < cutoff`` 的笔记。

    只给 arq cron 用(30 天自动清理)——业务查询一律走 ``Repo(owner_id)`` 作用域。
    返回 ``[(note_id, owner_id), ...]``,调用方据此用对应 owner 的 Repos 清理孤儿 source/blob。
    """
    rows = await session.execute(
        select(Note.id, Note.owner_id).where(
            Note.status == "archived",
            Note.archived_at.is_not(None),
            Note.archived_at < cutoff,
        )
    )
    targets = [(int(note_id), int(owner_id)) for note_id, owner_id in rows.all()]
    if not targets:
        return []
    await session.execute(
        delete(Note).where(Note.id.in_([note_id for note_id, _ in targets]))
    )
    return targets
