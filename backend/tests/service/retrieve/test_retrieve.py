"""检索测试(线 K):fake embedder + 外层事务隔离,不加载 bge、不连真 Redis。

隔离要点:测试场景用 owner_id=42/43 —— 开发库真实数据全是 owner 1,
owner 作用域让向量名次断言不受真实数据干扰(与 import 测试按 sha 过滤同理)。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Chunk
from app.repo import Repos
from app.service.imports.pipeline import EMBEDDING_MODEL
from app.service.retrieve import retrieve

DIM = 1024
OWNER_A = 42  # 测试场景专属 owner(真实数据全是 owner 1)
OWNER_B = 43

CHILD_TEXTS = {
    "closure": "闭包可以捕获外层作用域的变量,让函数记住定义时的环境",
    "async": "异步编程通过事件循环调度协程,避免阻塞主线程",
    "docker": "Docker 的镜像分层让构建可以复用缓存,加快部署速度",
}


def one_hot(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


async def _seed_note(
    factory: async_sessionmaker,
    *,
    owner_id: int = OWNER_A,
    status: str = "active",
    title: str = "测试笔记",
    dim_offset: int = 0,
) -> dict[str, Any]:
    """一个 note + 3 个 section(父块+子块);子块向量 = one_hot(dim_offset+0/1/2)。"""
    async with factory() as session:
        repos = Repos(session, owner_id)
        note = await repos.notes.create(
            source_id=None, title=title, doc={"type": "doc", "content": []}
        )
        note.status = status
        parents: dict[str, int] = {}
        children: dict[str, int] = {}
        for i, key in enumerate(CHILD_TEXTS):
            parent = Chunk(
                note_id=note.id,
                ord=i * 2,
                is_parent=True,
                heading_path=f"父标题-{key}",
                text=f"{key} 小节的父块全文,包含 {CHILD_TEXTS[key]}",
                token_count=50,
            )
            child = Chunk(
                note_id=note.id,
                parent=parent,
                ord=i * 2 + 1,
                is_parent=False,
                heading_path=f"父标题-{key}",
                text=CHILD_TEXTS[key],
                token_count=20,
            )
            session.add_all([parent, child])
            await session.flush()
            parents[key] = parent.id
            children[key] = child.id
        await repos.embeddings.bulk_insert(
            [
                (children[key], EMBEDDING_MODEL, DIM, one_hot(dim_offset + i))
                for i, key in enumerate(CHILD_TEXTS)
            ]
        )
        await session.commit()
        return {"note_id": note.id, "parents": parents, "children": children}


def test_vector_route_hits_closest_child_and_returns_parent(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        seed = await _seed_note(factory)
        result = await retrieve(
            "向量检索",
            top_k=5,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(0),  # = closure 子块的向量
            session_factory=factory,
        )
        return seed, result

    seed, result = asyncio.run(db_scope(scene))
    assert result.hits, "向量路应命中"
    top = result.hits[0]
    assert top.child_id == seed["children"]["closure"]
    assert top.parent_id == seed["parents"]["closure"]
    assert top.vector_rank == 1
    assert top.lexical_rank is None
    assert top.heading_path == "父标题-closure"
    assert top.child_text == CHILD_TEXTS["closure"]
    assert top.parent_text != top.child_text  # small-to-big:返回的是父块文本


def test_lexical_route_matches_text(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory)
        # 向量取未用过的维度:向量路仍有结果(正交,距离 1.0)但都不语义匹配;
        # 词法路应把 docker 子块顶到第 1
        return await retrieve(
            CHILD_TEXTS["docker"],
            top_k=5,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(500),
            session_factory=factory,
        )

    result = asyncio.run(db_scope(scene))
    docker_hit = next(h for h in result.hits if h.child_text == CHILD_TEXTS["docker"])
    assert docker_hit.lexical_rank == 1
    assert docker_hit.heading_path == "父标题-docker"
    others = [h for h in result.hits if h.child_text != CHILD_TEXTS["docker"]]
    assert all(h.lexical_rank is None for h in others)


def test_rrf_fuses_both_routes(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory)
        return await retrieve(
            CHILD_TEXTS["closure"],  # 词法完美命中(相似度 1.0)
            top_k=5,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(0),  # 向量也是第 1 名
            session_factory=factory,
        )

    result = asyncio.run(db_scope(scene))
    top = result.hits[0]
    assert top.child_text == CHILD_TEXTS["closure"]
    assert top.vector_rank == 1
    assert top.lexical_rank == 1
    assert abs(top.rrf_score - 2.0 / (60 + 1)) < 1e-5  # 双路各贡献 1/(k+1)


def test_archived_note_excluded(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory)
        # 归档笔记的子块有完美匹配向量(dim 10+0);若未过滤,它必占向量第 1
        await _seed_note(factory, status="archived", title="归档笔记", dim_offset=10)
        return await retrieve(
            "向量检索",
            top_k=5,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(10),
            session_factory=factory,
        )

    result = asyncio.run(db_scope(scene))
    assert result.hits
    assert all(h.note_title != "归档笔记" for h in result.hits)


def test_owner_isolation(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory, owner_id=OWNER_A, title="甲的笔记")
        await _seed_note(factory, owner_id=OWNER_B, title="乙的笔记", dim_offset=10)
        seen_by_a = await retrieve(
            "向量检索",
            top_k=5,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(10),  # 完美匹配乙的子块
            session_factory=factory,
        )
        seen_by_b = await retrieve(
            "向量检索",
            top_k=5,
            owner_id=OWNER_B,
            embed_func=lambda _q: one_hot(10),
            session_factory=factory,
        )
        return seen_by_a, seen_by_b

    seen_by_a, seen_by_b = asyncio.run(db_scope(scene))
    assert all(h.note_title != "乙的笔记" for h in seen_by_a.hits)  # 甲看不到乙
    assert seen_by_b.hits[0].note_title == "乙的笔记"
    assert seen_by_b.hits[0].vector_rank == 1


def test_parent_embedding_never_recalled(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        seed = await _seed_note(factory)
        async with factory() as session:
            repos = Repos(session, OWNER_A)
            # 给 closure 父块挂一个完美匹配向量(真实流程父块无向量;这里验证检索层也挡住)
            await repos.embeddings.bulk_insert(
                [(seed["parents"]["closure"], EMBEDDING_MODEL, DIM, one_hot(0))]
            )
            # 再造一个无子块的独立父块,同样挂完美向量
            orphan = Chunk(
                note_id=seed["note_id"],
                ord=99,
                is_parent=True,
                heading_path="孤儿父块",
                text="孤儿父块的全文,没有子块",
                token_count=10,
            )
            session.add(orphan)
            await session.flush()
            await repos.embeddings.bulk_insert([(orphan.id, EMBEDDING_MODEL, DIM, one_hot(0))])
            await session.commit()
            orphan_id = orphan.id
        result = await retrieve(
            "向量检索",
            top_k=5,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(0),
            session_factory=factory,
        )
        return seed, orphan_id, result

    seed, orphan_id, result = asyncio.run(db_scope(scene))
    child_ids = {h.child_id for h in result.hits}
    assert seed["parents"]["closure"] not in child_ids  # 父块即使有向量也不当「子块」召回
    assert orphan_id not in child_ids
    assert all(h.parent_id != orphan_id for h in result.hits)
    assert result.hits[0].child_id == seed["children"]["closure"]  # 第 1 仍是真子块


def test_top_k_limits_parent_hits(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory)
        return await retrieve(
            "向量检索",
            top_k=2,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(0),
            session_factory=factory,
        )

    result = asyncio.run(db_scope(scene))
    assert 0 < len(result.hits) <= 2


def test_owner_without_data_returns_empty(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory, owner_id=OWNER_A)
        return await retrieve(
            "向量检索",
            top_k=5,
            owner_id=999,  # 没有任何数据的 owner
            embed_func=lambda _q: one_hot(0),
            session_factory=factory,
        )

    result = asyncio.run(db_scope(scene))
    assert result.hits == []


def test_hit_carries_note_title_and_scores(db_scope) -> None:
    async def scene(factory: async_sessionmaker) -> Any:
        await _seed_note(factory, title="带标题的笔记")
        return await retrieve(
            "向量检索",
            top_k=1,
            owner_id=OWNER_A,
            embed_func=lambda _q: one_hot(0),
            session_factory=factory,
        )

    result = asyncio.run(db_scope(scene))
    hit = result.hits[0]
    assert hit.note_title == "带标题的笔记"
    assert hit.rrf_score > 0
    assert hit.note_id > 0 and hit.parent_id > 0 and hit.child_id > 0
