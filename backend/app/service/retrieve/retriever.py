"""检索服务:query → 双路召回(向量 + trgm 词法) → RRF 融合 → small-to-big。

- 命中的都是子块,返回给调用方的是其父块(小块检索、父块入上下文,ADR 0001);
  父块分数 = 其所有命中子块的 RRF 分之和,代表子块取该父块下 RRF 最高的子块。
- rerank = RRF 融合(k=60 标准值),不引 cross-encoder(不新增依赖,协调方确认);
- query 侧必须用 ``embed_query``(带指令前缀的非对称接口,AGENTS §5 决策 4 硬后果);
- 向量检索按 ``embeddings.model`` 过滤,模型名与导入流水线共用同一份常量(换模型口径见 ADR 0003)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Chunk
from app.repo import Repos
from app.service.embed import EMBEDDING_MODEL, embed_query

RRF_K = 60  # 标准 RRF 常数
RRF_PER_ROUTE_MIN = 20  # 每路进入融合的最少候选数

EmbedFunc = Callable[[str], object]


@dataclass
class RetrievalHit:
    """一个父块级命中:子块召回,父块出。"""

    parent_id: int
    parent_text: str
    heading_path: str | None
    note_id: int
    note_title: str
    rrf_score: float
    child_id: int  # 为该父块贡献最高 RRF 分的子块
    child_text: str
    vector_rank: int | None  # 代表子块在向量路的名次(1 起);None = 该路未命中
    lexical_rank: int | None


@dataclass
class RetrievalResult:
    query: str
    hits: list[RetrievalHit] = field(default_factory=list)


async def retrieve(
    query: str,
    *,
    top_k: int = 5,
    owner_id: int = 1,
    embed_func: EmbedFunc | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> RetrievalResult:
    """混合检索入口。``embed_func`` / ``session_factory`` 可注入(测试用假向量与隔离会话)。"""
    per_route = max(top_k * 3, RRF_PER_ROUTE_MIN)
    embed = embed_func or embed_query
    query_vec = [float(x) for x in embed(query)]

    factory = session_factory or _default_factory()
    async with factory() as session:
        repos = Repos(session, owner_id)
        vec_hits = await repos.search.vector_search(query_vec, per_route, model=EMBEDDING_MODEL)
        lex_hits = await repos.search.lexical_search(query, per_route)

        # ---- RRF 融合(按子块 id)----
        rrf: dict[int, float] = {}
        ranks: dict[int, tuple[int | None, int | None]] = {}
        chunks_by_id: dict[int, Chunk] = {}
        for rank, (chunk, _title) in enumerate(vec_hits, start=1):
            rrf[chunk.id] = rrf.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
            ranks[chunk.id] = (rank, None)
            chunks_by_id[chunk.id] = chunk
        for rank, (chunk, _title) in enumerate(lex_hits, start=1):
            rrf[chunk.id] = rrf.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
            vec_rank, _ = ranks.get(chunk.id, (None, None))
            ranks[chunk.id] = (vec_rank, rank)
            chunks_by_id[chunk.id] = chunk
        if not rrf:
            return RetrievalResult(query=query)

        # ---- 子 → 父聚合(small-to-big)----
        parent_ids = sorted({c.parent_id for c in chunks_by_id.values() if c.parent_id})
        parents = await repos.search.get_parents(parent_ids)
        titles: dict[int, str] = {}
        for chunk, title in (*vec_hits, *lex_hits):
            titles[chunk.note_id] = title

        parent_score: dict[int, float] = {}
        best_child: dict[int, int] = {}  # parent_id → 代表子块(该父块下 RRF 最高)
        for chunk_id, score in rrf.items():
            pid = chunks_by_id[chunk_id].parent_id
            if pid is None:
                continue
            parent_score[pid] = parent_score.get(pid, 0.0) + score
            current_best = best_child.get(pid)
            if current_best is None or score > rrf[current_best]:
                best_child[pid] = chunk_id

        hits: list[RetrievalHit] = []
        for pid, score in sorted(parent_score.items(), key=lambda kv: kv[1], reverse=True)[:top_k]:
            parent = parents.get(pid)
            if parent is None:  # 数据异常(子块缺父),跳过而不是崩
                continue
            child_id = best_child[pid]
            child = chunks_by_id[child_id]
            vec_rank, lex_rank = ranks[child_id]
            hits.append(
                RetrievalHit(
                    parent_id=pid,
                    parent_text=parent.text,
                    heading_path=parent.heading_path,
                    note_id=child.note_id,
                    note_title=titles.get(child.note_id, ""),
                    rrf_score=round(score, 6),
                    child_id=child_id,
                    child_text=child.text,
                    vector_rank=vec_rank,
                    lexical_rank=lex_rank,
                )
            )
        return RetrievalResult(query=query, hits=hits)


def _default_factory() -> async_sessionmaker[AsyncSession]:
    from app.core.db import session_factory

    return session_factory
