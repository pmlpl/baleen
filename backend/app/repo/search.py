"""search 仓储:检索查询(线 K)。只查 active 笔记的子块,全部带 owner_id 作用域。

- 向量路:embeddings.vec 的 halfvec 余弦距离(命中 hnsw halfvec_cosine_ops 索引);
  父块无向量且显式过滤 is_parent,天然不进候选(small-to-big 的「小」端只有子块)。
- 词法路:chunks.text 的 pg_trgm 相似度;GIN trgm 索引可加速包含类过滤,
  但「按相似度排序」仍需全量候选再排序,当前数据量(v1 单用户)可接受。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Chunk, Embedding, Note

from .base import Repo

# 词法路相似度下限:滤掉零重叠噪声。推导值,待真实 query 校准
MIN_SIMILARITY = 0.05


class SearchRepo(Repo):
    async def vector_search(
        self, query_vec: list[float], limit: int, *, model: str
    ) -> list[tuple[Chunk, str]]:
        """向量路:按余弦距离升序取子块,返回 (chunk, note_title)。"""
        stmt = (
            select(Chunk, Note.title)
            .select_from(Embedding)
            .join(Chunk, Chunk.id == Embedding.chunk_id)
            .join(Note, Note.id == Chunk.note_id)
            .where(
                Embedding.model == model,
                Chunk.is_parent.is_(False),
                Note.owner_id == self.owner_id,
                Note.status == "active",
            )
            .order_by(Embedding.vec.cosine_distance(query_vec))
            .limit(limit)
        )
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]

    async def lexical_search(
        self, query: str, limit: int, *, min_similarity: float = MIN_SIMILARITY
    ) -> list[tuple[Chunk, str]]:
        """词法路:按 trgm 相似度降序取子块,返回 (chunk, note_title)。"""
        sim = func.similarity(Chunk.text, query)
        stmt = (
            select(Chunk, Note.title)
            .join(Note, Note.id == Chunk.note_id)
            .where(
                sim > min_similarity,
                Chunk.is_parent.is_(False),
                Note.owner_id == self.owner_id,
                Note.status == "active",
            )
            .order_by(sim.desc())
            .limit(limit)
        )
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]

    async def get_parents(self, parent_ids: list[int]) -> dict[int, Chunk]:
        """子 → 父解析:按 id 取父块行。"""
        if not parent_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Chunk).where(Chunk.id.in_(parent_ids), Chunk.is_parent.is_(True))
            )
        ).all()
        return {row[0].id: row[0] for row in rows}
