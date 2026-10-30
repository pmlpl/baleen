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
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentOwnerDep, SessionDep
from app.api.library import RetrieveDep
from app.models.qa import QaMessage, QaSession
from app.repo.qa import QaRepo
from app.service.llm import LLMClient, LLMError
from app.service.llm.resolve import resolve_llm_config, resolve_tavily_key
from app.service.qa.answerer import (
    _EMPTY_ANSWER,
    ContextHit,
    QaResult,
    answer,
    answer_web,
    build_citations,
    build_qa_messages,
    build_web_citations,
)
from app.service.qa.rewriter import rewrite_question
from app.service.qa.summarizer import (
    NoteTopic,
    dialogue_for_range,
    organize_session_note,
    plan_note_split,
)
from app.service.qa.websearch import search_web

router = APIRouter(prefix="/api/qa", tags=["qa"])

MAX_QUERY_CHARS = 500
DEFAULT_TOP_K = 5
MAX_TOP_K = 20
TITLE_CHARS = 30  # 会话标题 = 首问前 30 字

# 导出落盘目录:gitignored 的 backend/tmp/qa_exports/,给协调方/agent 直接读取用
EXPORT_DIR = Path(__file__).resolve().parents[2] / "tmp" / "qa_exports"
# 阶段4(D2=B):会话产出的笔记落盘目录(「项目文件夹」愿景的最小落地)
NOTES_OUT_DIR = Path(__file__).resolve().parents[2] / "notes_out"


class ChatRequest(BaseModel):
    """POST /api/qa/chat 请求体:session_id 为空 = 新会话。"""

    session_id: int | None = None
    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    # 用户引用的笔记(@ 某篇):告诉 AI「在问什么」;检索照跑找关联笔记
    note_ids: list[int] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question 不能为空白")
        return stripped


class CitationOut(BaseModel):
    n: int
    # None = 联网出处(url 指向来源网页);int = 库内笔记出处
    note_id: int | None
    note_title: str
    heading_path: str | None
    snippet: str
    url: str | None = None


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
    used_web: bool  # True = 库内没找到,回答基于联网搜索(出处带 url)
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


async def get_llm_client(session: SessionDep, owner_id: CurrentOwnerDep) -> LLMClient:
    """LLM client 工厂:设置页(DB 单行)优先,回退 .env;每次请求现读 → 热生效。

    测试用 ``dependency_overrides`` 换成带 FakeTransport 的假 client。
    """
    base_url, api_key, model = await resolve_llm_config(session, owner_id)
    return LLMClient(base_url=base_url, api_key=api_key, model=model)


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]


def _cache_hit_rate(hit: int | None, miss: int | None) -> float | None:
    """hit/(hit+miss);双 0(该条没有缓存数据)→ None。"""
    h, m = hit or 0, miss or 0
    total = h + m
    return round(h / total, 4) if total else None


def _has_relevant_hit(hits: list[ContextHit], query: str) -> bool:
    """简单相关性检查:至少一条命中的正文包含查询里的某个关键词。

    向量检索永远返回 top_k 条近邻,即使完全不相关——这个检查防止
    「库内其实没有但检索返回了 5 条不相干的」时联网兜底不触发。

    分两类词:
    - **专有词**(英文 ≥3 字符):必须至少一个整词命中才算相关
      (避免 "ReAct" 拆成 re/ea/ac/ct 在代码里到处匹配)
    - **通用词**(中文 2 字滑窗):无专有词时用,任一命中即可
    """
    specific: set[str] = set()  # 英文整词
    generic: set[str] = set()  # 中文 2 字
    for part in re.split(r"[\s,，。、？?!！]+", query):
        part = part.strip()
        if not part:
            continue
        if re.search(r"[a-zA-Z]", part) and len(part) >= 3:
            specific.add(part.lower())
        elif len(part) >= 2:
            generic.add(part)
            for i in range(len(part) - 1):
                generic.add(part[i : i + 2])
    if not specific and not generic:
        return bool(hits)  # 查询太短无法拆词,有命中就算相关
    for h in hits:
        text = (h.parent_text or "").lower()
        if specific:
            if any(t in text for t in specific):
                return True
        elif any(t in text for t in generic):
            return True
    return False


REF_NOTE_MAX_CHARS = 8000  # 单篇引用笔记喂给 LLM 的正文上限(超长截头部)


async def _load_ref_notes(session: AsyncSession, owner_id: int, note_ids: list[int]) -> list[dict]:
    """加载用户引用的笔记(只取该用户的 active 笔记;非法/别人的 id 静默忽略)。

    正文 = 父块按 ord 拼接(与笔记详情页同一真源);去重保持首次引用顺序。
    """
    if not note_ids:
        return []
    from app.repo.library import LibraryRepo

    repo = LibraryRepo(session, owner_id)
    out: list[dict] = []
    seen: set[int] = set()
    for note_id in note_ids[:5]:  # 最多 5 篇,防 prompt 爆炸
        if note_id in seen:
            continue
        seen.add(note_id)
        note = await repo.get_active_note(note_id)
        if note is None:
            continue
        parents = await repo.list_parent_chunks(note_id)
        text = "\n\n".join(parent.text for parent in parents)[:REF_NOTE_MAX_CHARS]
        if not text.strip():
            continue
        out.append({"note_id": note.id, "title": note.title, "text": text})
    return out


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
    payload: ChatRequest,
    retrieve: RetrieveDep,
    llm: LLMClientDep,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
) -> ChatResponse:
    started = time.perf_counter()
    repo = QaRepo(session, owner_id)

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
    result = await retrieve(search_query, top_k=payload.top_k, owner_id=owner_id)
    kb_hits = [
        ContextHit(
            note_id=h.note_id,
            note_title=h.note_title,
            heading_path=h.heading_path,
            parent_text=h.parent_text,
            child_text=h.child_text,
        )
        for h in result.hits
    ]
    ref_notes = await _load_ref_notes(session, owner_id, payload.note_ids)
    messages, _build_meta = build_qa_messages(
        payload.question, kb_hits, history=history, ref_notes=ref_notes
    )
    llm_user_text = messages[-1]["content"]

    # 4.5) 工具链兜底:库内没命中 OR 命中但不相关(正文不含查询关键词) → 联网搜索
    web_results: list[dict[str, str]] = []
    tavily_key = await resolve_tavily_key(session, owner_id)
    kb_irrelevant = not kb_hits or not _has_relevant_hit(kb_hits, search_query)
    if kb_irrelevant and tavily_key:
        try:
            web_results = await asyncio.to_thread(search_web, search_query, api_key=tavily_key)
        except Exception:  # noqa: BLE001 —— 搜索失败走「没找到」口径,不让整轮失败
            web_results = []

    # 4) 存 user 消息(两份:content 给人看,llm_content 给下轮 LLM 重放)
    user_msg = await repo.add_message(
        qa_session.id, role="user", content=payload.question, llm_content=llm_user_text
    )

    # 5) LLM(同步 → to_thread,不阻塞事件循环):联网结果走 web 版,否则库内版
    used_web = bool(web_results)
    if used_web:
        qa: QaResult = await asyncio.to_thread(
            answer_web,
            payload.question,
            web_results,
            history=history,
            client=llm,
            ref_notes=ref_notes,
        )
    else:
        qa = await asyncio.to_thread(
            answer, payload.question, kb_hits, history=history, client=llm, ref_notes=ref_notes
        )
        # 二段兜底:LLM 用 [NO_KB] 标记「笔记答不了」→ 联网搜;没配 Tavily 就用它自己的
        # 知识正面回答(带标注)。相关性检查只认字面(「数据库」会命中发票笔记),
        # 语义层面的「答不了」由 LLM 自己判。
        if qa.answer.startswith("[NO_KB]"):
            body = qa.answer.removeprefix("[NO_KB]").strip()
            if tavily_key:
                try:
                    web_results = await asyncio.to_thread(
                        search_web, search_query, api_key=tavily_key
                    )
                except Exception:  # noqa: BLE001 —— 搜索失败走自身知识口径
                    web_results = []
            if web_results:
                qa = await asyncio.to_thread(
                    answer_web,
                    payload.question,
                    web_results,
                    history=history,
                    client=llm,
                    ref_notes=ref_notes,
                )
                used_web = True
            else:
                qa = qa.model_copy(
                    update={
                        "answer": "（笔记库里没有相关内容，以下用 AI 自身的知识回答）\n\n" + body,
                        "citations": [],
                    }
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
        used_web=used_web,
        took_ms=took_ms,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream_endpoint(
    payload: ChatRequest,
    retrieve: RetrieveDep,
    llm: LLMClientDep,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
) -> StreamingResponse:
    """流式问答:SSE 事件流 meta → delta* → done(error 兜底)。

    改写与检索在流开始**之前**完成(客户端等待期间显示「思考中」);
    轮次开始即 commit 用户消息(回答失败它也不丢),回答完成再 commit 助手消息。
    """
    started = time.perf_counter()
    repo = QaRepo(session, owner_id)

    if payload.session_id is None:
        qa_session = await repo.create_session(payload.question[:TITLE_CHARS])
    else:
        qa_session = await repo.get_session(payload.session_id)
        if qa_session is None:
            raise HTTPException(status_code=404, detail=f"会话不存在:{payload.session_id}")

    prior = await repo.list_messages(qa_session.id)
    history = [
        {
            "role": m.role,
            "content": (m.llm_content or m.content) if m.role == "user" else m.content,
        }
        for m in prior
    ]
    history_chat = [{"role": m.role, "content": m.content} for m in prior]

    search_query = payload.question
    if history:
        try:
            search_query = await asyncio.to_thread(
                rewrite_question, payload.question, history_chat, client=llm
            )
        except LLMError:
            search_query = payload.question

    result = await retrieve(search_query, top_k=payload.top_k, owner_id=owner_id)
    kb_hits = [
        ContextHit(
            note_id=h.note_id,
            note_title=h.note_title,
            heading_path=h.heading_path,
            parent_text=h.parent_text,
            child_text=h.child_text,
        )
        for h in result.hits
    ]
    ref_notes = await _load_ref_notes(session, owner_id, payload.note_ids)
    messages, _build_meta = build_qa_messages(
        payload.question, kb_hits, history=history, ref_notes=ref_notes
    )
    llm_user_text = messages[-1]["content"]

    # 工具链兜底:库内没命中 OR 命中但不相关 → 联网搜索
    web_results: list[dict[str, str]] = []
    tavily_key = await resolve_tavily_key(session, owner_id)
    kb_irrelevant = not kb_hits or not _has_relevant_hit(kb_hits, search_query)
    if kb_irrelevant and tavily_key:
        try:
            web_results = await asyncio.to_thread(search_web, search_query, api_key=tavily_key)
        except Exception:  # noqa: BLE001 —— 搜索失败走「没找到」口径,不让整轮失败
            web_results = []
    used_web = bool(web_results)

    user_msg = await repo.add_message(
        qa_session.id, role="user", content=payload.question, llm_content=llm_user_text
    )
    await session.commit()  # 轮次开始落库;回答完成后再 commit 助手消息
    user_out = _message_out(user_msg)

    async def gen():
        nonlocal used_web  # marker 兜底会改写;不声明会变成局部变量,meta 处就崩
        accumulated: list[str] = []
        usage_event: dict | None = None
        try:
            yield _sse(
                "meta",
                {
                    "session_id": qa_session.id,
                    "session_title": qa_session.title,
                    "search_query": search_query,
                    "used_web": used_web,
                    "model": llm.model,  # 本次回答用的模型,前端状态行显示
                    # 检索/改写花了多久(秒):前端据此区分「在检索」还是「在等模型」
                    "prepare_ms": int(round((time.perf_counter() - started) * 1000)),
                    "user_message": user_out.model_dump(),
                },
            )
            if web_results:
                # 联网兜底走非流式回答(web 上下文结构不同),整段作为一帧 delta 下发
                qa = await asyncio.to_thread(
                    answer_web,
                    payload.question,
                    web_results,
                    history=history,
                    client=llm,
                    ref_notes=ref_notes,
                )
                accumulated.append(qa.answer)
                yield _sse("delta", {"text": qa.answer})
                answer_text = qa.answer
                citations = build_web_citations(web_results, answer_text)
                cost_tokens = qa.cost_tokens or 0
                model_name = qa.model or llm.model
                cache_hit = cache_miss = 0
            else:
                iterator = llm.chat_stream(messages, max_tokens=3000)
                marker = False  # [NO_KB] = 笔记答不了,LLM 会给自身知识回答(不外发)
                buffered = ""
                streaming = False
                try:
                    while True:
                        # 同步生成器逐段推进放线程里,不阻塞事件循环
                        event = await asyncio.to_thread(next, iterator, None)
                        if event is None:
                            break
                        if event["type"] == "usage":
                            usage_event = event
                            continue
                        if event["type"] != "content":
                            continue
                        piece = event["text"]
                        if marker:
                            buffered += piece  # 静默收完自身知识回答
                            continue
                        if streaming:
                            accumulated.append(piece)
                            yield _sse("delta", {"text": piece})
                            continue
                        buffered += piece
                        combined = buffered.lstrip()
                        if combined.startswith("[NO_KB]"):
                            marker = True
                            continue
                        # 缓冲到能判定首行再放行,防止标记漏给用户
                        if "\n" in buffered or (
                            len(combined) >= 8 and not combined.startswith("[")
                        ):
                            streaming = True
                            accumulated.append(buffered)
                            yield _sse("delta", {"text": buffered})
                            buffered = ""
                        # else: 继续缓冲直到可判定
                finally:
                    close = getattr(iterator, "close", None)
                    if close is not None:
                        close()

                if marker:
                    # 二段兜底:联网搜;没配 Tavily 就用 LLM 已给出的自身知识回答
                    body = buffered.removeprefix("[NO_KB]").strip()
                    web_results2: list[dict[str, str]] = []
                    if tavily_key:
                        try:
                            web_results2 = await asyncio.to_thread(
                                search_web, search_query, api_key=tavily_key
                            )
                        except Exception:  # noqa: BLE001 —— 搜索失败走自身知识口径
                            web_results2 = []
                    if web_results2:
                        used_web = True
                        qa = await asyncio.to_thread(
                            answer_web,
                            payload.question,
                            web_results2,
                            history=history,
                            client=llm,
                            ref_notes=ref_notes,
                        )
                        answer_text = qa.answer
                        citations = build_web_citations(web_results2, answer_text)
                        cost_tokens = qa.cost_tokens or 0
                        model_name = qa.model or llm.model
                        cache_hit = cache_miss = 0
                        yield _sse("delta", {"text": answer_text})
                    else:
                        answer_text = (
                            "（笔记库里没有相关内容，以下用 AI 自身的知识回答）\n\n" + body
                        )
                        citations = []
                        cost_tokens = (usage_event or {}).get("total_tokens") or 0
                        model_name = llm.model
                        cache_hit = (usage_event or {}).get("prompt_cache_hit_tokens") or 0
                        cache_miss = (usage_event or {}).get("prompt_cache_miss_tokens") or 0
                        yield _sse("delta", {"text": answer_text})
                else:
                    if buffered:  # 短回答没到放行阈值,流结束后整段放行
                        accumulated.append(buffered)
                        yield _sse("delta", {"text": buffered})
                    answer_text = "".join(accumulated).strip() or _EMPTY_ANSWER
                    citations = build_citations(kb_hits, answer_text)
                    cost_tokens = (usage_event or {}).get("total_tokens") or 0
                    model_name = llm.model
                    cache_hit = (usage_event or {}).get("prompt_cache_hit_tokens") or 0
                    cache_miss = (usage_event or {}).get("prompt_cache_miss_tokens") or 0

            assistant_msg = await repo.add_message(
                qa_session.id,
                role="assistant",
                content=answer_text,
                citations=[c.model_dump() for c in citations],
                model=model_name,
                cost_tokens=cost_tokens,
                cache_hit_tokens=cache_hit,
                cache_miss_tokens=cache_miss,
            )
            await session.commit()
            yield _sse(
                "done",
                {
                    "assistant_message": _message_out(assistant_msg).model_dump(),
                    "took_ms": int(round((time.perf_counter() - started) * 1000)),
                },
            )
        except LLMError as exc:
            yield _sse("error", {"detail": str(exc)})
        except Exception:
            yield _sse("error", {"detail": "流式回答中断,请重试"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions", response_model=SessionsPage)
async def list_sessions(
    session: SessionDep,
    owner_id: CurrentOwnerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SessionsPage:
    total, rows = await QaRepo(session, owner_id).list_sessions(limit=limit)
    return SessionsPage(
        total=total,
        items=[SessionItem(id=s.id, title=s.title, created_at=s.created_at) for s in rows],
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: int, session: SessionDep, owner_id: CurrentOwnerDep
) -> SessionDetail:
    repo = QaRepo(session, owner_id)
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
async def delete_session(
    session_id: int, session: SessionDep, owner_id: CurrentOwnerDep
) -> None:
    deleted = await QaRepo(session, owner_id).delete_session(session_id)
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
async def export_session(
    session_id: int, session: SessionDep, owner_id: CurrentOwnerDep
) -> Response:
    """导出会话为 Markdown:浏览器下载一份,同时在 tmp/qa_exports/ 落一份给 agent 读。"""
    repo = QaRepo(session, owner_id)
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


class SaveNoteResponse(BaseModel):
    note_file: str
    organized_chars: int
    import_enqueued: bool
    import_job_id: str | None
    # 多篇模式(split/topics)的完整产物;旧字段取第一篇以保持兼容
    notes: list[SaveNoteItem] = Field(default_factory=list)


class SaveNoteItem(BaseModel):
    note_file: str
    title: str
    organized_chars: int
    import_enqueued: bool
    import_job_id: str | None


class NoteTopicOut(BaseModel):
    """note-plan 返回的一个主题;id 即 topics 数组下标。"""

    id: int
    title: str
    summary: str
    msg_start: int
    msg_end: int


class NotePlanResponse(BaseModel):
    topics: list[NoteTopicOut]
    suggestion: str  # "split"(主题无关,建议拆) | "single"(同一主题)


class SaveNoteRequest(BaseModel):
    """存为笔记的选项(不带 body = 旧行为:整段一篇)。

    mode: single=合成一篇;split=按计划每主题一篇;topics=只生成选中的主题。
    instruction: 用户自由输入的补充要求,拼进整理 prompt。
    """

    mode: Literal["single", "split", "topics"] = "single"
    topic_ids: list[int] = Field(default_factory=list)
    instruction: str = Field(default="", max_length=500)


def _sanitize_filename(title: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\s]+', "-", title).strip("-.")[:40] or "session"
    return safe if safe.endswith(".md") else f"{safe}.md"


async def _organize_and_enqueue(
    note_title: str, note_md: str, owner_id: int
) -> SaveNoteItem:
    """一篇笔记的落盘 + 入库(save-note 与多篇模式共用)。"""
    NOTES_OUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now():%Y-%m-%d}-{_sanitize_filename(note_title)}"
    (NOTES_OUT_DIR / filename).write_text(note_md, encoding="utf-8")

    # 入库:写暂存区 → no_llm 导入(G0 解析→G1→切块→向量化;跳过 G2,来源是自家整理产物)
    from app.worker.worker import STAGING_ROOT, enqueue_import

    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staging_path = STAGING_ROOT / filename
    staging_path.write_text(note_md, encoding="utf-8")
    dedupe_key = hashlib.sha256(note_md.encode("utf-8")).hexdigest()
    job_id = await enqueue_import(
        str(staging_path), dedupe_key=dedupe_key, no_llm=True, owner_id=owner_id
    )
    return SaveNoteItem(
        note_file=f"notes_out/{filename}",
        title=note_title,
        organized_chars=len(note_md),
        import_enqueued=job_id is not None,
        import_job_id=job_id,
    )


@router.post("/sessions/{session_id}/note-plan", response_model=NotePlanResponse)
async def note_plan(
    session_id: int, llm: LLMClientDep, session: SessionDep, owner_id: CurrentOwnerDep
) -> NotePlanResponse:
    """存笔记前的主题分析:跨主题的对话让用户选「拆几篇/要哪篇」,再生成。"""
    repo = QaRepo(session, owner_id)
    qa_session = await repo.get_session(session_id)
    if qa_session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    messages = await repo.list_messages(session_id)
    if len(messages) < 2:
        raise HTTPException(status_code=400, detail="会话还没有可整理的问答内容")

    plain = [{"role": m.role, "content": m.content} for m in messages]
    plan = await plan_note_split(qa_session.title, plain, client=llm)
    return NotePlanResponse(
        topics=[
            NoteTopicOut(
                id=i,
                title=t.title,
                summary=t.summary,
                msg_start=t.msg_start,
                msg_end=t.msg_end,
            )
            for i, t in enumerate(plan.topics)
        ],
        suggestion=plan.suggestion,
    )


@router.post("/sessions/{session_id}/save-note", response_model=SaveNoteResponse)
async def save_session_as_note(
    session_id: int,
    llm: LLMClientDep,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
    payload: SaveNoteRequest | None = None,
) -> SaveNoteResponse:
    """阶段4(D2=B):把会话整理成 Markdown 笔记 → 落 notes_out/ → no_llm 入库(异步)。

    直接入库跳过 G2(来源是自家整理产物);G0/G1/切块/向量化照常走,保证可检索。
    带 body 时支持跨主题拆分:split=每主题一篇,topics=只生成选中的;
    instruction 是用户自由输入的补充要求。
    """
    repo = QaRepo(session, owner_id)
    qa_session = await repo.get_session(session_id)
    if qa_session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    messages = await repo.list_messages(session_id)
    if len(messages) < 2:
        raise HTTPException(status_code=400, detail="会话还没有可整理的问答内容")

    req = payload or SaveNoteRequest()
    plain = [{"role": m.role, "content": m.content} for m in messages]
    instruction = req.instruction.strip()

    # 拆分/选主题 → 先拿计划(不信任前端传的区间,按 id 从本会话现算的计划里取)
    topics_for_notes: list[NoteTopic] = []
    if req.mode in ("split", "topics"):
        plan = await plan_note_split(qa_session.title, plain, client=llm)
        if plan.suggestion == "single" and len(plan.topics) == 1:
            # 单主题对话,拆分无意义 → 合成一篇(带上 instruction)
            topics_for_notes = []
        elif req.mode == "topics":
            picked = {i for i in req.topic_ids if 0 <= i < len(plan.topics)}
            topics_for_notes = (
                [t for i, t in enumerate(plan.topics) if i in picked] or list(plan.topics)
            )
        else:
            topics_for_notes = list(plan.topics)

    items: list[SaveNoteItem] = []
    if not topics_for_notes:
        dialogue = "\n\n".join(
            f"{'用户' if m.role == 'user' else '助手'}:{m.content}" for m in messages
        )
        note_md = await organize_session_note(
            qa_session.title, dialogue, instruction=instruction, client=llm
        )
        items.append(await _organize_and_enqueue(qa_session.title, note_md, owner_id))
    else:
        # 每主题一篇,串行(D3 口径);区间外的闲聊不带进单篇,保持笔记聚焦
        for topic in topics_for_notes:
            dialogue = dialogue_for_range(plain, topic.msg_start, topic.msg_end)
            note_title = topic.title if topic.title else qa_session.title
            note_md = await organize_session_note(
                note_title, dialogue, instruction=instruction, client=llm
            )
            items.append(await _organize_and_enqueue(note_title, note_md, owner_id))

    first = items[0]
    return SaveNoteResponse(
        note_file=first.note_file,
        organized_chars=first.organized_chars,
        import_enqueued=first.import_enqueued,
        import_job_id=first.import_job_id,
        notes=items,
    )
