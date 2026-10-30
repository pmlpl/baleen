"""chunks 仓储:父块/子块落库(ADR 0003 §2.4)。父块不写 embedding 由上层保证。"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from app.models import Chunk as ChunkRow
from app.service.chunk import Chunk, ChunkResult

from .base import Repo


class ChunksRepo(Repo):
    async def delete_by_note(self, note_id: int) -> int:
        """删光一个笔记的 chunks,返回删除行数。

        子块/父块一起删(self-FK CASCADE);embeddings 靠 FK CASCADE 顺带清。
        供标题回填等「整篇重建切块」的场景用。
        """
        result = await self.session.execute(
            delete(ChunkRow).where(ChunkRow.note_id == note_id)
        )
        return result.rowcount or 0

    async def get_by_note(self, note_id: int) -> list[ChunkRow]:
        rows = await self.session.scalars(
            select(ChunkRow).where(ChunkRow.note_id == note_id).order_by(ChunkRow.ord)
        )
        return list(rows)

    async def count_by_note(self, note_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).select_from(ChunkRow).where(ChunkRow.note_id == note_id)
            )
            or 0
        )

    async def bulk_insert(self, note_id: int, result: ChunkResult) -> list[tuple[Chunk, ChunkRow]]:
        """插入一个切块结果:先父块(flush 拿 id),再子块并挂 parent_id。

        返回子块的 (切块对象, ORM 行),供向量化阶段只给子块写 embeddings。
        """
        parent_rows: dict[int, ChunkRow] = {}
        for chunk in result.chunks:
            if not chunk.is_parent:
                continue
            row = ChunkRow(
                note_id=note_id,
                parent_id=None,
                ord=chunk.ord,
                heading_path=chunk.heading_path,
                text=chunk.text,
                token_count=chunk.token_count,
                is_parent=True,
                meta=chunk.meta,
            )
            self.session.add(row)
            parent_rows[chunk.section_idx] = row
        await self.session.flush()  # 父块 id 必须落定,子块才能挂 parent_id

        children: list[tuple[Chunk, ChunkRow]] = []
        for chunk in result.chunks:
            if chunk.is_parent:
                continue
            parent = parent_rows.get(chunk.section_idx)
            row = ChunkRow(
                note_id=note_id,
                parent_id=parent.id if parent is not None else None,
                ord=chunk.ord,
                heading_path=chunk.heading_path,
                text=chunk.text,
                token_count=chunk.token_count,
                is_parent=False,
                meta=chunk.meta,
            )
            self.session.add(row)
            children.append((chunk, row))
        await self.session.flush()
        return children
