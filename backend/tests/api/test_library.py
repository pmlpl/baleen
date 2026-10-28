"""查询/检索 API 测试(线 M):TestClient + 外层事务隔离;检索走 fake retriever。

不加载 bge 模型、不碰 embeddings 表:`/api/search` 的检索依赖用 ``dependency_overrides``
换成假实现(真链路由人工自测 + ``retrieve_smoke`` 覆盖)。

两条隔离技巧(开发库里本来就有真实数据,不能假设库是空的):
1. 种下的行 ``created_at`` 一律设到 2099 → 必排在真实数据(2026)之前,排序/分页断言稳定;
2. ``filename`` / ``title`` 带本文件的唯一标记 → 过滤断言与库里已有多少行无关。
列表接口的 ``total`` 一律与**直连数据库**算出的 ``count(*)`` 对帐,证明它不是 ``len(items)``。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.api import library as library_module
from app.models import Chunk, Source
from app.repo import Repos

FUTURE_NEW = datetime(2099, 12, 31, tzinfo=UTC)
FUTURE_OLD = datetime(2099, 6, 1, tzinfo=UTC)

SOURCE_ITEM_FIELDS = {
    "id",
    "filename",
    "ext",
    "size_bytes",
    "parse_status",
    "message",
    "note_id",
    "created_at",
}
NOTE_ITEM_FIELDS = {
    "id",
    "title",
    "lang",
    "tags",
    "quality",
    "status",
    "source_id",
    "chunk_count",
    "created_at",
}
NOTE_DETAIL_FIELDS = {
    "id",
    "title",
    "lang",
    "tags",
    "quality",
    "status",
    "source_id",
    "markdown",
    "parents",
}
PARENT_FIELDS = {"chunk_id", "ord", "heading_path", "text", "token_count"}
HIT_FIELDS = {
    "parent_id",
    "parent_text",
    "heading_path",
    "note_id",
    "note_title",
    "rrf_score",
    "child_id",
    "child_text",
    "vector_rank",
    "lexical_rank",
}

PortalFn = Callable[..., Awaitable[Any]]


@dataclass
class FakeHit:
    """字段名与 ``service.retrieve.RetrievalHit`` 完全一致。

    故意不 import 那个模块:它会拉起 sentence-transformers/torch,而 API 单测不该付这个代价。
    真字段对齐由人工自测的真实链路(真模型 + 真库)兜底。
    """

    parent_id: int
    parent_text: str
    heading_path: str | None
    note_id: int
    note_title: str
    rrf_score: float
    child_id: int
    child_text: str
    vector_rank: int | None
    lexical_rank: int | None


@dataclass
class FakeResult:
    query: str
    hits: list[FakeHit] = field(default_factory=list)


# ---------------------------------------------------------------- 工具 / 造数


def _portal_call(env: SimpleNamespace, fn: PortalFn, *args: Any, **kwargs: Any) -> Any:
    """在 TestClient 的 portal 循环上跑一段异步代码(和请求同一个事件循环/连接)。

    用 ``partial`` 包一层:anyio 的 ``BlockingPortal.call`` 只收位置参数。
    """
    return env.client.portal.call(partial(fn, *args, **kwargs))


async def _seed_source(
    factory: Any,
    *,
    filename: str,
    parse_status: str = "ok",
    created_at: datetime = FUTURE_NEW,
    message: str | None = None,
) -> int:
    sha = hashlib.sha256(os.urandom(16)).hexdigest()
    async with factory() as session:
        repos = Repos(session, 1)
        source, _ = await repos.sources.create_or_get_by_sha256(
            sha256=sha,
            filename=filename,
            ext=".md",
            size_bytes=1234,
            storage_path="unused",
            parse_status=parse_status,
        )
        source.created_at = created_at
        if message is not None:
            source.parse_meta = {"message": message}
        await session.commit()
        return source.id


async def _seed_note(
    factory: Any,
    *,
    title: str,
    created_at: datetime = FUTURE_NEW,
    source_id: int | None = None,
    status: str = "active",
    n_parents: int = 2,
    n_children: int = 3,
    marker: str = "seed",
) -> dict[str, Any]:
    """一条 note + n_parents 个父块 + n_children 个子块(子块挂在父块下)。"""
    async with factory() as session:
        repos = Repos(session, 1)
        note = await repos.notes.create(
            source_id=source_id,
            title=title,
            doc={"type": "doc", "content": []},
            quality="core",
            markdown="# 标题\n\n正文段落。",
            lang="zh",
        )
        note.tags = ["笔记"]
        note.created_at = created_at
        note.status = status
        parents: list[Chunk] = []
        for index in range(n_parents):
            parent = Chunk(
                note_id=note.id,
                ord=index * 10,
                is_parent=True,
                heading_path=f"第{index}章 > 概述",
                text=f"{marker} 父块 {index} 的全文",
                token_count=300 + index,
            )
            session.add(parent)
            parents.append(parent)
        await session.flush()
        for index in range(n_children):
            session.add(
                Chunk(
                    note_id=note.id,
                    parent_id=parents[index % len(parents)].id,
                    ord=index * 10 + 1,
                    is_parent=False,
                    heading_path=f"第{index % len(parents)}章 > 概述",
                    text=f"{marker} 子块 {index}",
                    token_count=120,
                )
            )
        await session.flush()
        ids = {"note_id": note.id, "parent_ids": [parent.id for parent in parents]}
        await session.commit()
        return ids


async def _count_sources(factory: Any, parse_status: str | None = None) -> int:
    stmt = select(func.count()).select_from(Source).where(Source.owner_id == 1)
    if parse_status is not None:
        stmt = stmt.where(Source.parse_status == parse_status)
    async with factory() as session:
        return int(await session.scalar(stmt) or 0)


async def _count_chunks(factory: Any, note_id: int) -> int:
    stmt = select(func.count()).select_from(Chunk).where(Chunk.note_id == note_id)
    async with factory() as session:
        return int(await session.scalar(stmt) or 0)


@pytest.fixture
def fake_retrieve(env: SimpleNamespace) -> Any:
    """把 /api/search 的检索依赖换成假实现:记录调用参数,返回 ``state.hits``。"""
    from app.main import app

    state = SimpleNamespace(hits=[], calls=[])

    async def fake(query: str, *, top_k: int = 5, owner_id: int = 1, **_kwargs: Any) -> FakeResult:
        state.calls.append({"query": query, "top_k": top_k, "owner_id": owner_id})
        return FakeResult(query=query, hits=list(state.hits))

    target = library_module.get_retrieve
    previous = app.dependency_overrides.get(target)
    app.dependency_overrides[target] = lambda: fake
    try:
        yield state
    finally:
        if previous is None:
            app.dependency_overrides.pop(target, None)
        else:
            app.dependency_overrides[target] = previous


# ---------------------------------------------------------------- GET /api/sources


def test_list_sources_paginates_newest_first_with_real_total(env: SimpleNamespace) -> None:
    _portal_call(
        env, _seed_source, env.shared["factory"], filename="线M-旧.md", created_at=FUTURE_OLD
    )
    _portal_call(env, _seed_source, env.shared["factory"], filename="线M-新.md")

    first = env.client.get("/api/sources", params={"limit": 1, "offset": 0})
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"total", "items"}
    assert len(body["items"]) == 1
    assert body["items"][0]["filename"] == "线M-新.md"  # created_at DESC
    assert set(body["items"][0]) == SOURCE_ITEM_FIELDS  # 契约字段,一个不多一个不少
    assert "review_runs" not in body["items"][0]  # 列表不带重字段
    assert body["items"][0]["note_id"] is None  # 这条 source 没有笔记

    # total 是真 count(*),不是 len(items)
    assert body["total"] == _portal_call(env, _count_sources, env.shared["factory"])
    assert body["total"] >= 2 > len(body["items"])

    second = env.client.get("/api/sources", params={"limit": 1, "offset": 1})
    assert second.json()["items"][0]["filename"] == "线M-旧.md"  # 分页确实翻页了


def test_list_sources_filter_by_parse_status_and_message(env: SimpleNamespace) -> None:
    _portal_call(
        env,
        _seed_source,
        env.shared["factory"],
        filename="线M-扫描件.pdf",
        parse_status="no_text_expected",
        message="扫描件没有文本层,需要 OCR",
    )

    resp = env.client.get("/api/sources", params={"parse_status": "no_text_expected"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"]
    assert all(item["parse_status"] == "no_text_expected" for item in body["items"])
    assert body["total"] == _portal_call(
        env, _count_sources, env.shared["factory"], "no_text_expected"
    )

    seeded = next(item for item in body["items"] if item["filename"] == "线M-扫描件.pdf")
    assert seeded["message"] == "扫描件没有文本层,需要 OCR"  # 来自 parse_meta.message


def test_list_sources_shows_note_id(env: SimpleNamespace) -> None:
    source_id = _portal_call(env, _seed_source, env.shared["factory"], filename="线M-带笔记.md")
    seed = _portal_call(
        env, _seed_note, env.shared["factory"], title="线M-带笔记", source_id=source_id
    )

    body = env.client.get("/api/sources", params={"limit": 1}).json()
    assert body["items"][0]["filename"] == "线M-带笔记.md"
    assert body["items"][0]["note_id"] == seed["note_id"]  # 批量补齐,不是 null


# ---------------------------------------------------------------- GET /api/notes


def test_list_notes_chunk_count_counts_parents_only(env: SimpleNamespace) -> None:
    seed = _portal_call(
        env,
        _seed_note,
        env.shared["factory"],
        title="线M-父块计数",
        n_parents=2,
        n_children=3,
    )

    resp = env.client.get("/api/notes", params={"q": "线M-父块计数"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1  # 唯一标记,开发库里没有别的笔记会命中
    item = body["items"][0]
    assert set(item) == NOTE_ITEM_FIELDS
    assert item["id"] == seed["note_id"]
    assert item["chunk_count"] == 2  # 父块数(section 数)
    # 注意:契约示例里写的是 "quality": "high",但库的 CHECK 只允许 core/normal/reference/junk
    # (ADR 0003 §2.3)。这里按库里真实取值断言,契约示例的偏差已写进 DELIVERY 的已知限制。
    assert item["lang"] == "zh" and item["tags"] == ["笔记"] and item["quality"] == "core"
    assert item["status"] == "active"
    # 库里该笔记共 5 个块(2 父 + 3 子):chunk_count 若按全部块算会是 5
    assert _portal_call(env, _count_chunks, env.shared["factory"], seed["note_id"]) == 5


def test_list_notes_q_is_ilike_and_orders_desc(env: SimpleNamespace) -> None:
    _portal_call(env, _seed_note, env.shared["factory"], title="线M-排序-旧", created_at=FUTURE_OLD)
    _portal_call(env, _seed_note, env.shared["factory"], title="线M-排序-新")
    _portal_call(env, _seed_note, env.shared["factory"], title="无关的笔记标题")

    body = env.client.get("/api/notes", params={"q": "线M-排序"}).json()
    assert [item["title"] for item in body["items"]] == ["线M-排序-新", "线M-排序-旧"]
    assert body["total"] == 2

    page = env.client.get("/api/notes", params={"q": "线M-排序", "limit": 1, "offset": 1}).json()
    assert page["total"] == 2  # 分页不影响 total
    assert [item["title"] for item in page["items"]] == ["线M-排序-旧"]


def test_list_notes_hides_archived(env: SimpleNamespace) -> None:
    _portal_call(
        env,
        _seed_note,
        env.shared["factory"],
        title="线M-已归档",
        status="archived",
    )
    body = env.client.get("/api/notes", params={"q": "线M-已归档"}).json()
    assert body == {"total": 0, "items": []}  # 软删除(ADR §3⑤):对外不可见


# ---------------------------------------------------------------- GET /api/notes/{id}


def test_note_detail_returns_fields_and_parents_in_ord_order(env: SimpleNamespace) -> None:
    source_id = _portal_call(env, _seed_source, env.shared["factory"], filename="线M-详情.md")
    seed = _portal_call(
        env,
        _seed_note,
        env.shared["factory"],
        title="线M-详情",
        source_id=source_id,
        n_parents=3,
        n_children=2,
        marker="线M-详情",
    )

    resp = env.client.get(f"/api/notes/{seed['note_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == NOTE_DETAIL_FIELDS
    assert body["id"] == seed["note_id"]
    assert body["title"] == "线M-详情"
    assert body["source_id"] == source_id
    assert body["markdown"] == "# 标题\n\n正文段落。"
    assert body["tags"] == ["笔记"]

    parents = body["parents"]
    assert len(parents) == 3  # 只出父块(2 个子块不在里面)
    assert [parent["ord"] for parent in parents] == [0, 10, 20]  # ord ASC
    assert all(set(parent) == PARENT_FIELDS for parent in parents)
    assert parents[0]["chunk_id"] == seed["parent_ids"][0]
    assert parents[0]["heading_path"] == "第0章 > 概述"
    assert parents[0]["text"] == "线M-详情 父块 0 的全文"
    assert parents[0]["token_count"] == 300


def test_note_detail_not_found_returns_chinese_404(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/notes/987654321")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "笔记不存在" in detail


def test_note_detail_hides_archived(env: SimpleNamespace) -> None:
    seed = _portal_call(
        env, _seed_note, env.shared["factory"], title="线M-归档详情", status="archived"
    )
    resp = env.client.get(f"/api/notes/{seed['note_id']}")
    assert resp.status_code == 404


# ---------------------------------------------------------------- POST /api/search


def test_search_maps_hit_fields(env: SimpleNamespace, fake_retrieve: SimpleNamespace) -> None:
    fake_retrieve.hits.extend(
        [
            FakeHit(
                parent_id=55,
                parent_text="父块全文……",
                heading_path="第一章 > 概述",
                note_id=7,
                note_title="读书笔记",
                rrf_score=0.0328,
                child_id=61,
                child_text="命中的那个子块……",
                vector_rank=1,
                lexical_rank=1,
            ),
            FakeHit(
                parent_id=56,
                parent_text="另一个父块",
                heading_path=None,
                note_id=8,
                note_title="另一篇笔记",
                rrf_score=0.0164,
                child_id=62,
                child_text="只被向量路召回",
                vector_rank=2,
                lexical_rank=None,
            ),
        ]
    )

    resp = env.client.post("/api/search", json={"query": "内存泄漏怎么排查", "top_k": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"query", "took_ms", "hits"}
    assert body["query"] == "内存泄漏怎么排查"
    assert isinstance(body["took_ms"], int) and body["took_ms"] >= 0
    assert fake_retrieve.calls == [
        {"query": "内存泄漏怎么排查", "top_k": 2, "owner_id": 1}
    ]  # top_k 透传、owner 写死 1

    first, second = body["hits"]
    assert set(first) == HIT_FIELDS
    assert first == {
        "parent_id": 55,
        "parent_text": "父块全文……",
        "heading_path": "第一章 > 概述",
        "note_id": 7,
        "note_title": "读书笔记",
        "rrf_score": 0.0328,
        "child_id": 61,
        "child_text": "命中的那个子块……",
        "vector_rank": 1,
        "lexical_rank": 1,
    }
    assert second["vector_rank"] == 2 and second["lexical_rank"] is None  # 该路没召回到 = null


def test_search_empty_hits_is_ok_not_404(
    env: SimpleNamespace, fake_retrieve: SimpleNamespace
) -> None:
    resp = env.client.post("/api/search", json={"query": "库里没有相关内容"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["hits"] == []
    assert fake_retrieve.calls[0]["top_k"] == 5  # 默认 top_k=5


def test_search_strips_query_and_echoes_normalized(
    env: SimpleNamespace, fake_retrieve: SimpleNamespace
) -> None:
    resp = env.client.post("/api/search", json={"query": "  闭包的作用域链  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["query"] == "闭包的作用域链"
    assert fake_retrieve.calls[0]["query"] == "闭包的作用域链"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "\n\t "},
        {"query": "字" * 501},
        {"query": "闭包", "top_k": 0},
        {"query": "闭包", "top_k": 21},
    ],
)
def test_search_rejects_bad_input_422(
    env: SimpleNamespace, fake_retrieve: SimpleNamespace, payload: dict[str, Any]
) -> None:
    resp = env.client.post("/api/search", json=payload)
    assert resp.status_code == 422, resp.text
    assert fake_retrieve.calls == []  # 参数不合法时压根不检索(不白加载模型)


def test_search_accepts_boundary_values(
    env: SimpleNamespace, fake_retrieve: SimpleNamespace
) -> None:
    resp = env.client.post("/api/search", json={"query": "字" * 500, "top_k": 20})
    assert resp.status_code == 200, resp.text
    assert fake_retrieve.calls == [{"query": "字" * 500, "top_k": 20, "owner_id": 1}]


def test_notes_and_sources_share_the_same_note(env: SimpleNamespace) -> None:
    """列表与详情对同一条笔记给出一致的 id / chunk_count / 父块集合。"""
    source_id = _portal_call(env, _seed_source, env.shared["factory"], filename="线M-一致.md")
    seed = _portal_call(
        env,
        _seed_note,
        env.shared["factory"],
        title="线M-一致性",
        source_id=source_id,
        n_parents=2,
        n_children=4,
    )

    listed = env.client.get("/api/notes", params={"q": "线M-一致性"}).json()["items"][0]
    detail = env.client.get(f"/api/notes/{seed['note_id']}").json()
    assert listed["id"] == detail["id"] == seed["note_id"]
    assert listed["chunk_count"] == len(detail["parents"]) == 2
    assert listed["source_id"] == detail["source_id"] == source_id
    # 时间必须是带时区的 ISO-8601(库里是 timestamptz,pydantic 把 UTC 序列化成 ``...Z``)
    created_at = datetime.fromisoformat(listed["created_at"])
    assert created_at.tzinfo is not None
    assert created_at.astimezone(UTC) == FUTURE_NEW
