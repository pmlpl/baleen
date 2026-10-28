"""查询与检索 API(线 M):GET /api/sources · GET /api/notes · GET /api/notes/{id} · POST /api/search。

契约唯一依据是 ``docs/api.md`` §3 / §4,**字段名与状态值一个字都不改**。要点:

- 列表的 ``total`` 一律来自真实 ``count(*)``(不是 ``len(items)``);
  ``chunk_count`` / ``note_id`` 都用一次批量查询补齐,不在循环里逐条查(N+1);
- ``GET /api/notes/{note_id}`` 只出父块(``is_parent=true``,``ord ASC``);
- ``POST /api/search`` 直接复用 ``app.service.retrieve.retriever.retrieve``(small-to-big + RRF
  已在线 K 实现),本层只做「契约映射 + ``took_ms`` 计时」;``hits`` 为空是**正常结果**(200 + ``[]``);
- 检索入口走**延迟导入 + 依赖注入**:API 进程启动不因 import 把 torch / 解析栈拉起来
  (与 upload.py 对 pipeline 的处理同一取舍),测试也因此能把检索换成假实现而不加载 bge。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.deps import SessionDep
from app.repo.library import LibraryRepo

router = APIRouter(prefix="/api", tags=["library"])

OWNER_ID = 1  # 单用户,写死 1(AGENTS §5 决策 11);业务表都有 owner_id 列,查询只走 Repo

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_TOP_K = 5
MAX_TOP_K = 20
MAX_QUERY_CHARS = 500


# ---------------------------------------------------------------- 响应模型(照契约)


class SourceListItem(BaseModel):
    """GET /api/sources 的一条:**不含** review_runs(列表别带重字段)。"""

    id: int
    filename: str
    ext: str
    size_bytes: int
    parse_status: str
    message: str | None
    note_id: int | None
    created_at: datetime


class SourcesPage(BaseModel):
    total: int
    items: list[SourceListItem]


class NoteListItem(BaseModel):
    id: int
    title: str
    lang: str | None
    tags: list[str]
    quality: str | None
    status: str
    source_id: int | None
    chunk_count: int  # 父块数(section 数),不是子块
    created_at: datetime


class NotesPage(BaseModel):
    total: int
    items: list[NoteListItem]


class ParentChunkOut(BaseModel):
    chunk_id: int
    ord: int
    heading_path: str | None
    text: str
    token_count: int


class NoteDetailOut(BaseModel):
    id: int
    title: str
    lang: str | None
    tags: list[str]
    quality: str | None
    status: str
    source_id: int | None
    markdown: str | None
    parents: list[ParentChunkOut]


class SearchRequest(BaseModel):
    """POST /api/search 请求体:query 1–500 字(纯空白算空),top_k 1–20 默认 5。"""

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)

    @field_validator("query")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query 不能为空白")
        return stripped


class SearchHitOut(BaseModel):
    parent_id: int
    parent_text: str
    heading_path: str | None
    note_id: int
    note_title: str
    rrf_score: float
    child_id: int
    child_text: str
    vector_rank: int | None  # None = 该路没召回到(两路都命中 = 最可信)
    lexical_rank: int | None


class SearchResponse(BaseModel):
    query: str
    took_ms: int
    hits: list[SearchHitOut]


# ---------------------------------------------------------------- 检索入口(延迟导入)


class RetrieveCallable(Protocol):
    """``retriever.retrieve`` 的结构类型。

    只做类型标注用途:真模块要 import sentence-transformers/torch,不该在 API 进程启动时加载。
    """

    def __call__(
        self, query: str, *, top_k: int = ..., owner_id: int = ..., **kwargs: Any
    ) -> Awaitable[Any]: ...


def get_retrieve() -> RetrieveCallable:
    """返回检索入口(延迟导入);测试用 ``dependency_overrides`` 换成假实现。"""
    from app.service.retrieve.retriever import retrieve

    return retrieve


RetrieveDep = Annotated[RetrieveCallable, Depends(get_retrieve)]

LimitQuery = Annotated[
    int, Query(ge=1, le=MAX_LIMIT, description=f"每页条数,默认 {DEFAULT_LIMIT},上限 {MAX_LIMIT}")
]
OffsetQuery = Annotated[int, Query(ge=0, description="偏移,默认 0")]


# ---------------------------------------------------------------- 路由


@router.get("/sources", response_model=SourcesPage)
async def list_sources(
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
    parse_status: Annotated[str | None, Query(description="按解析状态过滤,可省略")] = None,
) -> SourcesPage:
    repo = LibraryRepo(session, OWNER_ID)
    total, rows = await repo.list_sources(parse_status=parse_status, limit=limit, offset=offset)
    note_ids = await repo.note_ids_by_source([source.id for source in rows])
    items = [
        SourceListItem(
            id=source.id,
            filename=source.filename,
            ext=source.ext,
            size_bytes=source.size_bytes,
            parse_status=source.parse_status,
            message=(source.parse_meta or {}).get("message"),
            note_id=note_ids.get(source.id),
            created_at=source.created_at,
        )
        for source in rows
    ]
    return SourcesPage(total=total, items=items)


@router.get("/notes", response_model=NotesPage)
async def list_notes(
    session: SessionDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
    q: Annotated[str | None, Query(description="标题模糊匹配(ILIKE),不走向量")] = None,
) -> NotesPage:
    repo = LibraryRepo(session, OWNER_ID)
    total, rows = await repo.list_notes(q=q, limit=limit, offset=offset)
    counts = await repo.parent_chunk_counts([note.id for note in rows])
    items = [
        NoteListItem(
            id=note.id,
            title=note.title,
            lang=note.lang,
            tags=list(note.tags or []),
            quality=note.quality,
            status=note.status,
            source_id=note.source_id,
            chunk_count=counts.get(note.id, 0),
            created_at=note.created_at,
        )
        for note in rows
    ]
    return NotesPage(total=total, items=items)


@router.get("/notes/{note_id}", response_model=NoteDetailOut)
async def get_note_detail(note_id: int, session: SessionDep) -> NoteDetailOut:
    repo = LibraryRepo(session, OWNER_ID)
    note = await repo.get_active_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"笔记不存在:{note_id}")
    parents = await repo.list_parent_chunks(note.id)
    return NoteDetailOut(
        id=note.id,
        title=note.title,
        lang=note.lang,
        tags=list(note.tags or []),
        quality=note.quality,
        status=note.status,
        source_id=note.source_id,
        markdown=note.markdown,
        parents=[
            ParentChunkOut(
                chunk_id=chunk.id,
                ord=chunk.ord,
                heading_path=chunk.heading_path,
                text=chunk.text,
                token_count=chunk.token_count,
            )
            for chunk in parents
        ],
    )


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(payload: SearchRequest, retrieve: RetrieveDep) -> SearchResponse:
    started = time.perf_counter()
    # 直接复用线 K 的检索:双路召回 + RRF 融合 + small-to-big 全在 service 层,这里只做映射
    result = await retrieve(payload.query, top_k=payload.top_k, owner_id=OWNER_ID)
    took_ms = int(round((time.perf_counter() - started) * 1000))
    return SearchResponse(
        query=payload.query,
        took_ms=took_ms,
        hits=[
            SearchHitOut(
                parent_id=hit.parent_id,
                parent_text=hit.parent_text,
                heading_path=hit.heading_path,
                note_id=hit.note_id,
                note_title=hit.note_title,
                rrf_score=hit.rrf_score,
                child_id=hit.child_id,
                child_text=hit.child_text,
                vector_rank=hit.vector_rank,
                lexical_rank=hit.lexical_rank,
            )
            for hit in result.hits
        ],
    )
