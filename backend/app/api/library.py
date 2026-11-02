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

import asyncio
import logging
import time
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentOwnerDep, SessionDep
from app.repo.library import LibraryRepo

router = APIRouter(prefix="/api", tags=["library"])

logger = logging.getLogger(__name__)


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
    # 导入时是否跑过标题结构二次加工(前端展示徽章用)
    headings_rewritten: bool = False
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


class NoteImageOut(BaseModel):
    """笔记里的一张图(文档顺序)。``media`` 有值就走 /api/media/{key} 取原图。"""

    alt: str
    media: str | None
    src: str | None


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
    # v16:文档顺序的图片清单;前端按 [图片] alt 标记原位替换成真实插图
    images: list[NoteImageOut] = []


class NoteEditRequest(BaseModel):
    """PUT /api/notes/{id} 请求体:两个都省略没意义;同内容重复保存是 no-op。"""

    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    markdown: str | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title 不能是空白")
        return value

    @field_validator("markdown")
    @classmethod
    def _markdown_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("markdown 不能是空白")
        return value


class NoteEditOut(BaseModel):
    id: int
    title: str | None
    title_changed: bool
    content_changed: bool
    chunks_parents: int
    chunks_children: int
    embeddings: int


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
    owner_id: CurrentOwnerDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
    parse_status: Annotated[str | None, Query(description="按解析状态过滤,可省略")] = None,
) -> SourcesPage:
    repo = LibraryRepo(session, owner_id)
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
            headings_rewritten=bool((source.parse_meta or {}).get("headings_rewritten")),
            created_at=source.created_at,
        )
        for source in rows
    ]
    return SourcesPage(total=total, items=items)


@router.get("/notes", response_model=NotesPage)
async def list_notes(
    session: SessionDep,
    owner_id: CurrentOwnerDep,
    limit: LimitQuery = DEFAULT_LIMIT,
    offset: OffsetQuery = 0,
    q: Annotated[str | None, Query(description="标题模糊匹配(ILIKE),不走向量")] = None,
) -> NotesPage:
    repo = LibraryRepo(session, owner_id)
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


def _collect_images(doc: dict | None) -> list[NoteImageOut]:
    """文档顺序收集 image 节点(含容器内嵌套),供前端原位替换 [图片] 标记。"""
    found: list[NoteImageOut] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            if node.get("type") == "image":
                attrs = node.get("attrs") or {}
                found.append(
                    NoteImageOut(
                        alt=attrs.get("alt") or "",
                        media=attrs.get("media") or None,
                        src=attrs.get("src") or None,
                    )
                )
            walk(node.get("content") or [])

    walk((doc or {}).get("content") or [])
    return found


@router.get("/notes/{note_id}", response_model=NoteDetailOut)
async def get_note_detail(
    note_id: int, session: SessionDep, owner_id: CurrentOwnerDep
) -> NoteDetailOut:
    repo = LibraryRepo(session, owner_id)
    note = await repo.get_active_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"笔记不存在:{note_id}")
    parents = await repo.list_parent_chunks(note.id)
    # markdown 由 doc 现场序列化(编辑器工作稿);notes.markdown 列从未生成过,不信它
    from app.service.notes_edit import doc_to_markdown  # 延迟导入:启动不拉 torch

    return NoteDetailOut(
        id=note.id,
        title=note.title,
        lang=note.lang,
        tags=list(note.tags or []),
        quality=note.quality,
        status=note.status,
        source_id=note.source_id,
        markdown=doc_to_markdown(note.doc),
        images=_collect_images(note.doc),
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


@router.put("/notes/{note_id}", response_model=NoteEditOut)
async def edit_note(
    note_id: int, payload: NoteEditRequest, session: SessionDep, owner_id: CurrentOwnerDep
) -> NoteEditOut:
    """用户自编辑:改标题/改内容;内容编辑会换 doc 并重建切块+向量(同事务,失败回滚)。

    不跑 LLM——用户写什么就是什么;回填幂等闸不动,后续回填不会覆写用户 edits。
    **含图笔记也可改正文**:图片以 ``![alt](baleen-image:N)`` 标记往返,保存时按 N
    重新关联编辑前的原图片节点(见 ``notes_edit`` 模块说明)。
    """
    if payload.title is None and payload.markdown is None:
        raise HTTPException(status_code=422, detail="title 和 markdown 至少给一个")
    repo = LibraryRepo(session, owner_id)
    note = await repo.get_active_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"笔记不存在:{note_id}")

    from app.repo.repos import Repos
    from app.service.notes_edit import apply_note_edit

    def _embed(texts: list[str]) -> Any:
        from app.service.embed import embed_passage  # 延迟导入:首次编辑才加载 bge

        return embed_passage(texts)

    repos = Repos(session, owner_id)
    outcome = await apply_note_edit(
        session,
        repos,
        note,
        title=payload.title,
        markdown=payload.markdown,
        embed_func=_embed,
    )
    note_after = await repo.get_active_note(note_id)
    return NoteEditOut(
        id=outcome.note_id,
        title=note_after.title if note_after is not None else "",
        title_changed=outcome.title_changed,
        content_changed=outcome.content_changed,
        chunks_parents=outcome.chunks_parents,
        chunks_children=outcome.chunks_children,
        embeddings=outcome.embeddings,
    )


def _unlink_blob(path: str) -> None:
    """删原文件 blob(best-effort):文件没了不影响删除结果,失败记日志不抛。"""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除笔记原文件失败(%s):%s", path, exc)


@router.get("/media/{key}")
async def get_media(key: str) -> FileResponse:
    """取笔记里引用的图片(md 导入时按内容寻址存的字节)。

    ``key`` 是 sha256 hex;只做 key → 路径的映射,**不接受任何路径输入**。
    media 不带 owner 归属:key 本身就是内容哈希,知道 key 才取得到,
    且同一内容多用户共享一份文件(与源文件 blob 同一思路)。
    """
    from app.service.media import media_mime, media_path  # 延迟导入

    path = media_path(key)
    if path is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, media_type=media_mime(key))


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: int, session: SessionDep, owner_id: CurrentOwnerDep) -> None:
    """删除笔记(真删除,不是软删):

    - chunks/embeddings 由 DB 级联(FK ondelete=CASCADE)一并清空,检索立刻不再命中;
    - source 行在没有其他笔记引用时一并删除,原文件 blob 从事务提交后从磁盘清除
      (best-effort,失败只记日志);qa 消息里的引用角标会 404,引用文字不受影响。
    """
    note = await LibraryRepo(session, owner_id).get_active_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"笔记不存在:{note_id}")

    from app.repo.repos import Repos  # 与 edit_note 同款延迟导入

    repos = Repos(session, owner_id)
    deleted = await repos.notes.delete_active(note_id)
    if not deleted:  # 极端并发:检查与删除之间被别的请求删了,按 404 口径
        raise HTTPException(status_code=404, detail=f"笔记不存在:{note_id}")

    blob_path: str | None = None
    if note.source_id is not None:
        # sources:notes = 1:N(ADR 0003);还有别的笔记挂着这个 source 就只删笔记
        if await repos.sources.count_notes_for(note.source_id) == 0:
            blob_path = await repos.sources.delete(note.source_id)
    await session.commit()

    if blob_path is not None:
        await asyncio.to_thread(_unlink_blob, blob_path)


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(
    payload: SearchRequest, retrieve: RetrieveDep, owner_id: CurrentOwnerDep
) -> SearchResponse:
    started = time.perf_counter()
    # 直接复用线 K 的检索:双路召回 + RRF 融合 + small-to-big 全在 service 层,这里只做映射
    result = await retrieve(payload.query, top_k=payload.top_k, owner_id=owner_id)
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
