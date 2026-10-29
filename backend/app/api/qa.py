"""问答 API(阶段2:聊天式多轮)。

- POST   /api/qa/chat            提问(session_id 为空自动建会话);存 user/assistant 两条消息
- GET    /api/qa/sessions        会话列表(侧栏)
- GET    /api/qa/sessions/{id}   重开会话(全部消息)
- DELETE /api/qa/sessions/{id}   删会话(消息靠 FK CASCADE 连带删)

无状态的 ``POST /api/qa`` 已被替换(唯一消费者 QaView 同步改造)。
复用线 K 的 ``retrieve``(延迟导入,启动不拉 torch);LLM 同步调用用 ``asyncio.to_thread``;
LLM 异常向上抛(与 G2 同口径)。一条提问 = 建/找会话 + 存两条消息,原子(失败整体回滚)。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from app.api.deps import SessionDep
from app.api.library import OWNER_ID, RetrieveDep
from app.models.qa import QaMessage, QaSession
from app.repo.qa import QaRepo
from app.service.llm import LLMClient, LLMError
from app.service.qa.answerer import ContextHit, QaResult, answer, build_qa_messages
from app.service.qa.rewriter import rewrite_question

router = APIRouter(prefix="/api/qa", tags=["qa"])

MAX_QUERY_CHARS = 500
DEFAULT_TOP_K = 5
MAX_TOP_K = 20
TITLE_CHARS = 30  # 会话标题 = 首问前 30 字

# 导出落盘目录:gitignored 的 backend/tmp/qa_exports/,给协调方/agent 直接读取用
EXPORT_DIR = Path(__file__).resolve().parents[2] / "tmp" / "qa_exports"


class ChatRequest(BaseModel):
    """POST /api/qa/chat 请求体:session_id 为空 = 新会话。"""

    session_id: int | None = None
    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)

    @field_validator("question")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question 不能为空白")
        return stripped


class CitationOut(BaseModel):
    n: int
    note_id: int
    note_title: str
    heading_path: str | None
    snippet: str


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[CitationOut]
    model: str | None
    cost_tokens: int
    cache_hit_rate: float | None  # None = 该条没有缓存数据(user 消息 / 首调双 0)


class ChatResponse(BaseModel):
    session_id: int
    session_title: str
    user_message: MessageOut
    assistant_message: MessageOut
    search_query: str  # 实际用于检索的词(追问会先经 LLM 改写,与原话可能不同)
    took_ms: int


class SessionItem(BaseModel):
    id: int
    title: str
    created_at: datetime


class SessionsPage(BaseModel):
    total: int
    items: list[SessionItem]


class SessionDetail(BaseModel):
    id: int
    title: str
    created_at: datetime
    messages: list[MessageOut]


def get_llm_client() -> LLMClient:
    """LLM client 工厂;测试用 ``dependency_overrides`` 换成带 FakeTransport 的假 client。"""
    return LLMClient.from_settings()


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]


def _cache_hit_rate(hit: int | None, miss: int | None) -> float | None:
    """hit/(hit+miss);双 0(该条没有缓存数据)→ None。"""
    h, m = hit or 0, miss or 0
    total = h + m
    return round(h / total, 4) if total else None


def _message_out(m: QaMessage) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        citations=[CitationOut(**c) for c in (m.citations or [])],
        model=m.model,
        cost_tokens=m.cost_tokens,
        cache_hit_rate=_cache_hit_rate(m.cache_hit_tokens, m.cache_miss_tokens),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, retrieve: RetrieveDep, llm: LLMClientDep, session: SessionDep
) -> ChatResponse:
    started = time.perf_counter()
    repo = QaRepo(session, OWNER_ID)

    # 1) 会话:无则建(title = 首问前 30 字)
    if payload.session_id is None:
        qa_session = await repo.create_session(payload.question[:TITLE_CHARS])
    else:
        qa_session = await repo.get_session(payload.session_id)
        if qa_session is None:
            raise HTTPException(status_code=404, detail=f"会话不存在:{payload.session_id}")

    # 2) 历史两版(在插入新消息之前读,天然不含本条):
    #    llm_content 重放版给 answer(会话内前缀对齐 → 缓存命中);
    #    content 可读版给改写器(它只需要「聊了什么」,不带旧检索片段)。
    prior = await repo.list_messages(qa_session.id)
    history = [
        {
            "role": m.role,
            "content": (m.llm_content or m.content) if m.role == "user" else m.content,
        }
        for m in prior
    ]
    history_chat = [{"role": m.role, "content": m.content} for m in prior]

    # 3) 检索词:追问先经 LLM 改写成独立完整的问题(best-effort,改写失败回退原问题)
    search_query = payload.question
    if history:
        try:
            search_query = await asyncio.to_thread(
                rewrite_question, payload.question, history_chat, client=llm
            )
        except LLMError:
            search_query = payload.question

    # 4) 检索 top-k + 组装本轮 LLM 消息(拿到含 <context> 的原文,存 llm_content 供下轮重放)
    result = await retrieve(search_query, top_k=payload.top_k, owner_id=OWNER_ID)
    hits = [
        ContextHit(
            note_id=h.note_id,
            note_title=h.note_title,
            heading_path=h.heading_path,
            parent_text=h.parent_text,
            child_text=h.child_text,
        )
        for h in result.hits
    ]
    messages, _build_meta = build_qa_messages(payload.question, hits, history=history)
    llm_user_text = messages[-1]["content"]

    # 4) 存 user 消息(两份:content 给人看,llm_content 给下轮 LLM 重放)
    user_msg = await repo.add_message(
        qa_session.id, role="user", content=payload.question, llm_content=llm_user_text
    )

    # 5) LLM(同步 → to_thread,不阻塞事件循环)
    qa: QaResult = await asyncio.to_thread(
        answer, payload.question, hits, history=history, client=llm
    )

    # 5) 存 assistant 消息(出处 + 计费 + 缓存计数),整体 commit(失败全回滚)
    assistant_msg = await repo.add_message(
        qa_session.id,
        role="assistant",
        content=qa.answer,
        citations=[c.model_dump() for c in qa.citations],
        model=qa.model,
        cost_tokens=qa.cost_tokens or 0,
        cache_hit_tokens=qa.prompt_cache_hit_tokens or 0,
        cache_miss_tokens=qa.prompt_cache_miss_tokens or 0,
    )
    await session.commit()

    took_ms = int(round((time.perf_counter() - started) * 1000))
    return ChatResponse(
        session_id=qa_session.id,
        session_title=qa_session.title,
        user_message=_message_out(user_msg),
        assistant_message=_message_out(assistant_msg),
        search_query=search_query,
        took_ms=took_ms,
    )


@router.get("/sessions", response_model=SessionsPage)
async def list_sessions(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> SessionsPage:
    total, rows = await QaRepo(session, OWNER_ID).list_sessions(limit=limit)
    return SessionsPage(
        total=total,
        items=[SessionItem(id=s.id, title=s.title, created_at=s.created_at) for s in rows],
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(session_id: int, session: SessionDep) -> SessionDetail:
    repo = QaRepo(session, OWNER_ID)
    qa_session = await repo.get_session(session_id)
    if qa_session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    messages = await repo.list_messages(session_id)
    return SessionDetail(
        id=qa_session.id,
        title=qa_session.title,
        created_at=qa_session.created_at,
        messages=[_message_out(m) for m in messages],
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: int, session: SessionDep) -> None:
    deleted = await QaRepo(session, OWNER_ID).delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    await session.commit()


def _export_markdown(qa_session: QaSession, messages: list[QaMessage]) -> str:
    """会话 → Markdown(人可直接读,agent 可直接解析);出处/缓存/计费都带上。"""
    lines = [
        f"# 问答会话导出:{qa_session.title}",
        "",
        f"- 会话 id:{qa_session.id}",
        f"- 创建时间:{qa_session.created_at:%Y-%m-%d %H:%M:%S}",
        f"- 消息数:{len(messages)}",
        f"- 导出时间:{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "---",
        "",
    ]
    for i, m in enumerate(messages, start=1):
        who = "用户" if m.role == "user" else "助手"
        head = f"## [{i}] {who} · {m.created_at:%Y-%m-%d %H:%M:%S}"
        if m.role == "assistant" and m.model:
            rate = _cache_hit_rate(m.cache_hit_tokens, m.cache_miss_tokens)
            rate_text = f"{rate * 100:.1f}%" if rate is not None else "—"
            head += f"(model={m.model} · {m.cost_tokens} tok · 缓存命中率 {rate_text})"
        lines += [head, "", m.content, ""]
        if m.citations:
            lines += ["**出处**", ""]
            for c in m.citations:
                path = c.get("heading_path") or ""
                lines.append(f"{c['n']}. 《{c['note_title']}》{path}")
                snippet = c.get("snippet") or ""
                if snippet:
                    lines.append(f"   > {snippet}")
            lines.append("")
        lines += ["---", ""]
    return "\n".join(lines)


@router.get("/sessions/{session_id}/export")
async def export_session(session_id: int, session: SessionDep) -> Response:
    """导出会话为 Markdown:浏览器下载一份,同时在 tmp/qa_exports/ 落一份给 agent 读。"""
    repo = QaRepo(session, OWNER_ID)
    qa_session = await repo.get_session(session_id)
    if qa_session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    messages = await repo.list_messages(session_id)
    md = _export_markdown(qa_session, messages)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORT_DIR / f"session-{session_id}.md").write_text(md, encoding="utf-8")

    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.md"'},
    )
