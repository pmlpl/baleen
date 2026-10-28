"""embeddings 仓储:只给子块写向量(ADR 0003 §2.5)。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Chunk as ChunkRow
from app.models import Embedding

from .base import Repo


class EmbeddingsRepo(Repo):
    async def existing_chunk_ids(self, chunk_ids: list[int], model: str) -> set[int]:
        if not chunk_ids:
            return set()
        rows = await self.session.scalars(
            select(Embedding.chunk_id).where(
                Embedding.chunk_id.in_(chunk_ids), Embedding.model == model
            )
        )
        return set(rows)

    async def count_by_note(self, note_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Embedding)
                .join(ChunkRow, ChunkRow.id == Embedding.chunk_id)
                .where(ChunkRow.note_id == note_id)
            )
            or 0
        )

    async def bulk_insert(self, rows: list[tuple[int, str, int, list[float]]]) -> int:
        """rows = [(chunk_id, model, dim, vec), ...];返回插入行数。"""
        if not rows:
            return 0
        self.session.add_all(
            [
                Embedding(chunk_id=chunk_id, model=model, dim=dim, vec=vec)
                for chunk_id, model, dim, vec in rows
            ]
        )
        await self.session.flush()
        return len(rows)
