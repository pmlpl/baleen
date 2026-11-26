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
import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentOwnerDep, SessionDep
from app.api.library import RetrieveCallable, RetrieveDep
from app.models.qa import QaMessage, QaSession
from app.repo.qa import KEEP_SESSIONS, QaRepo
from app.service.llm import LLMClient, LLMError, estimate_tokens
from app.service.llm.resolve import (
    resolve_agent_enabled,
    resolve_llm_config,
    resolve_nickname,
    resolve_tavily_key,
)
from app.service.qa.agent import run_agent, run_agent_iter
from app.service.qa.answerer import (
    _EMPTY_ANSWER,
    ContextHit,
    QaResult,
    answer,
    answer_web,
    attach_images,
    build_citations,
    build_qa_messages,
    build_web_citations,
)
from app.service.qa.digest import DIGEST_FREEZE_BATCH, DIGEST_TRIGGER_TOKENS
from app.service.qa.rewriter import rewrite_question
from app.service.qa.summarizer import (
    NoteTopic,
    append_citation_appendix,
    note_dialogue,
    organize_session_note,
    plan_note_split,
)
from app.service.qa.websearch import search_web

router = APIRouter(prefix="/api/qa", tags=["qa"])

logger = logging.getLogger(__name__)

# 提问长度上限:原 500 常把长提示词挡成 422;放宽到远超模型上下文的量级,
# 不再因 question 字符数校验失败,实际长度仍受 LLM 上下文约束(超了是模型层错误)
MAX_QUERY_CHARS = 200_000
DEFAULT_TOP_K = 5
MAX_TOP_K = 20
TITLE_CHARS = 30  # 会话标题 = 首问前 30 字

# 导出落盘目录:gitignored 的 backend/tmp/qa_exports/,给协调方/agent 直接读取用
EXPORT_DIR = Path(__file__).resolve().parents[2] / "tmp" / "qa_exports"
# 阶段4(D2=B):会话产出的笔记落盘目录(「项目文件夹」愿景的最小落地)
NOTES_OUT_DIR = Path(__file__).resolve().parents[2] / "notes_out"


class AttachmentInput(BaseModel):
    """对话附件:用户临时上传给 AI 看的参考文件文本(不入库,纯对话上下文)。

    前端把文本类文件读成 text 透传;PDF/DOCX/PPTX/图片等二进制走导入笔记库路径。
    """

    name: str = Field(min_length=1, max_length=200)
    # 上限与 /api/parse 的 FILE_FULL_CHARS 对齐:小文件全文直塞,大文件在解析层已被
    # 分析成报告,这里不再收 20000 那种小上限(20000 是纯正文前的旧口径,太保守)
    text: str = Field(min_length=1, max_length=40000)


class ImageInput(BaseModel):
    """对话图片附件:用户临时上传给 AI 看的图片(不入库),走视觉通道。

    前端把图片读成 data URL(``data:<mime>;base64,…``)透传;后端复用
    ``attach_images``(OpenAI 视觉 content 数组)附到最后一条 user 消息。
    与命中笔记图(hit_images)合并附图,但不受其 MAX_HIT_IMAGES 上限约束。
    """

    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=1, max_length=100)
    data_url: str = Field(min_length=1, max_length=8_000_000)


class ChatRequest(BaseModel):
    """POST /api/qa/chat 请求体:session_id 为空 = 新会话。"""

    session_id: int | None = None
    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    # 用户引用的笔记(@ 某篇):告诉 AI「在问什么」;检索照跑找关联笔记
    note_ids: list[int] = Field(default_factory=list)
    # 对话附件(参考文件文本):不入库,注入 prompt 的 <reference_files> 块;≤5 个
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=5)
    # 对话图片附件(参考图):不入库,走视觉通道 attach_images;≤4 张
    images: list[ImageInput] = Field(default_factory=list, max_length=4)

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
    # 原始计数:前端用来算会话级「平均缓存命中率」Σhit/Σ(hit+miss),
    # 比按轮次平均各轮 rate 更接近真实的 token 级缓存效率
    cache_hit_tokens: int
    cache_miss_tokens: int
    # agent 步骤留痕(ADR 0010 授权审计):每步 {step, kind, tool, arg, result, latency_ms}。
    # None = 老路径(决策树)回答 / user 消息;开关开且回答出自 agent loop 才有值。
    agent_trace: list[dict] | None = None


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


class RenameSessionRequest(BaseModel):
    """PATCH /api/qa/sessions/{id} 请求体:重命名会话(侧栏行内编辑/右键菜单)。"""

    title: str = Field(min_length=1, max_length=TITLE_CHARS)

    @field_validator("title")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title 不能为空白")
        return stripped


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
    - **专有词**(英文连续 ≥3 字母):必须至少一个整词命中才算相关
      (避免 "ReAct" 拆成 re/ea/ac/ct 在代码里到处匹配)
    - **通用词**(中文 2 字滑窗):无专有词时用,任一命中即可

    中英连写(如「给我讲讲关于workflow」)时,把英文段单独切出来,
    否则整段会被当成一个英文词、要求整串匹配永远不中 → 误判不相关 → 误触联网。
    """
    specific: set[str] = set()  # 英文整词(连续 ≥3 字母)
    generic: set[str] = set()  # 中文 2 字滑窗
    for part in re.split(r"[\s,，。、？?!！]+", query):
        part = part.strip()
        if not part:
            continue
        # 抽连续字母(≥3)作英文专有词;中英连写时英文段单独切出来(见上注)
        en_runs = re.findall(r"[A-Za-z][A-Za-z]{2,}", part)
        if en_runs:
            specific.update(r.lower() for r in en_runs)
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


# ---- 命中图片笔记回传原图(caption 路线的后半步,ADR 0006)----
# 检索靠识别出的文字;回答时把原图一并给视觉模型「看着补细节」。
_HIT_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
MAX_HIT_IMAGES = 2
MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def _load_hit_images(
    session: AsyncSession, owner_id: int, kb_hits: list[ContextHit]
) -> list[dict]:
    """按命中顺序找出「来自图片文件」的笔记,读原图转 data URL;最多 MAX_HIT_IMAGES 张。

    大图/读不到/不是图片来源的命中静默跳过——回传原图是增强,不能让它拖垮回答。
    """
    ids: list[int] = []
    for h in kb_hits:
        if h.note_id not in ids:
            ids.append(h.note_id)
    if not ids:
        return []
    from sqlalchemy import select

    from app.models import Note, Source

    rows = (
        await session.execute(
            select(Note.id, Source.ext, Source.storage_path)
            .join(Source, Note.source_id == Source.id)
            .where(Note.id.in_(ids), Note.owner_id == owner_id)
        )
    ).all()
    by_note = {nid: (ext, sp) for nid, ext, sp in rows}
    images: list[dict] = []
    for nid in ids:
        if len(images) >= MAX_HIT_IMAGES:
            break
        ext, storage_path = by_note.get(nid, (None, None))
        mime = _HIT_IMAGE_MIME.get(ext or "")
        if mime is None or not storage_path:
            continue
        try:
            raw = await asyncio.to_thread(Path(storage_path).read_bytes)
        except OSError:
            continue
        if len(raw) > MAX_IMAGE_BYTES:
            continue
        images.append(
            {
                "note_id": nid,
                "mime": mime,
                "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
            }
        )
    return images


def _message_out(m: QaMessage) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        citations=[CitationOut(**c) for c in (m.citations or [])],
        model=m.model,
        cost_tokens=m.cost_tokens,
        cache_hit_rate=_cache_hit_rate(m.cache_hit_tokens, m.cache_miss_tokens),
        cache_hit_tokens=m.cache_hit_tokens,
        cache_miss_tokens=m.cache_miss_tokens,
        agent_trace=m.trace,
    )


async def _create_session_and_trim(repo: QaRepo, title: str, *, owner_id: int) -> QaSession:
    """新建会话 + 保留策略裁剪:只留最新 KEEP_SESSIONS 个会话,更旧的硬删(级联删消息/摘要)。

    两条消息流(非流式/SSE)共用;裁剪只在新建会话后触发(只有新会话出现才可能超上限)。
    删了几个记日志留痕,不建表。
    """
    qa_session = await repo.create_session(title)
    trimmed = await repo.trim_sessions()
    if trimmed:
        logger.info(
            "会话保留策略:owner=%s 删除 %d 个旧会话(只保留最新 %d 个)",
            owner_id,
            trimmed,
            KEEP_SESSIONS,
        )
    return qa_session


# ------------------------------------------------ chat / chat_stream 共享(C1,2026-11-24)
# 审查报告 C1:chat 与 chat_stream 曾各自复制一整套决策树(预检联网 → 库内回答 →
# [NO_KB] 二段兜底)。现两端点消费下面同一套共享实现,流式/非流式只差三件事:
# ① 事件包装(流式把共享事件映射成 SSE,非流式丢弃 status/delta 只取 result);
# ② 库内回答机制(非流式 answer() 一次拿全文 vs 流式 llm.chat_stream + [NO_KB] 首行门);
# ③ user 消息落库时点(流式轮次开始即 commit,回答失败 user 消息不丢;非流式整体一事务)。
# 对外行为零变化(等价性基线见 tests/api/test_qa_equivalence.py,先于重构写、跑绿)。


@dataclass
class _ChatRound:
    """一轮问答的准备结果(``_prepare_chat_round`` 产出,两端点同源消费)。"""

    repo: QaRepo
    payload: ChatRequest
    llm: LLMClient
    owner_id: int
    qa_session: QaSession
    window_msgs: list
    digest_summaries: list[str]
    history: list[dict[str, str]]
    history_chat: list[dict[str, str]]
    search_query: str
    kb_hits: list[ContextHit]
    ref_notes: list[dict]
    attachments: list[dict]
    user_images: list[dict]
    messages: list[dict[str, str]]
    llm_user_text: str
    tavily_key: str | None
    agent_enabled: bool
    nickname: str | None


async def _prepare_chat_round(
    payload: ChatRequest,
    *,
    retrieve: RetrieveCallable,
    llm: LLMClient,
    session: AsyncSession,
    owner_id: int,
) -> _ChatRound:
    """两端点共享的准备段:会话(无则建+保留裁剪)→ 历史两版 → 改写 → 检索 →
    组装本轮 LLM 消息 → 联网 key / agent 开关。原两份逐字重复,现同一实现。"""
    repo = QaRepo(session, owner_id)

    # 1) 会话:无则建(title = 首问前 30 字;建新会话触发保留策略裁剪,只留最新 50 个)
    if payload.session_id is None:
        qa_session = await _create_session_and_trim(
            repo, payload.question[:TITLE_CHARS], owner_id=owner_id
        )
    else:
        qa_session = await repo.get_session(payload.session_id)
        if qa_session is None:
            raise HTTPException(status_code=404, detail=f"会话不存在:{payload.session_id}")

    # 2) 历史两版(在插入新消息之前读,天然不含本条):
    #    近窗 = 最后一段 digest 之后的消息(无 digest 则全部);老对话已冻结成 digest,
    #    作为第二条 system 重放(见 answerer.compose_digest_block),不再原文重放——
    #    既止住超长会话静默丢失,又用不可变分段保住前缀缓存命中。
    #    llm_content 重放版给 answer(近窗前缀对齐 → 缓存命中);
    #    content 可读版给改写器(它只需要「聊了什么」,不带旧检索片段)。
    digests = await repo.list_digests(qa_session.id)
    last_end = digests[-1].end_msg_id if digests else 0
    window_msgs = await repo.list_messages_after(qa_session.id, last_end)
    digest_summaries = [d.summary for d in digests]
    history = [
        {
            "role": m.role,
            "content": (m.llm_content or m.content) if m.role == "user" else m.content,
        }
        for m in window_msgs
    ]
    history_chat = [{"role": m.role, "content": m.content} for m in window_msgs]

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
    attachments = [{"name": a.name, "text": a.text} for a in payload.attachments]
    user_images = [{"mime": i.mime, "data_url": i.data_url} for i in payload.images]
    tavily_key = await resolve_tavily_key(session, owner_id)
    agent_enabled = await resolve_agent_enabled(session, owner_id)
    nickname = await resolve_nickname(session, owner_id)  # None = prompt 与现状字节一致
    messages, _build_meta = build_qa_messages(
        payload.question,
        kb_hits,
        history=history,
        nickname=nickname,
        ref_notes=ref_notes,
        attachments=attachments,
        digest_summaries=digest_summaries,
    )
    return _ChatRound(
        repo=repo,
        payload=payload,
        llm=llm,
        owner_id=owner_id,
        qa_session=qa_session,
        window_msgs=window_msgs,
        digest_summaries=digest_summaries,
        history=history,
        history_chat=history_chat,
        search_query=search_query,
        kb_hits=kb_hits,
        ref_notes=ref_notes,
        attachments=attachments,
        user_images=user_images,
        messages=messages,
        llm_user_text=messages[-1]["content"],
        tavily_key=tavily_key,
        agent_enabled=agent_enabled,
        nickname=nickname,
    )


async def _search_web_safe(
    rnd: _ChatRound, *, meta_out: dict | None = None
) -> list[dict[str, str]]:
    """决策树联网搜索统一入口(原 qa.py 里 5 处同形调用点合一):两级链的
    fallback_base_url/fallback_api_key 参数与降级留痕(meta_out)原样保留(64c3b61);
    任何失败走「没找到」口径,不让整轮失败。"""
    try:
        return await asyncio.to_thread(
            search_web,
            rnd.search_query,
            api_key=rnd.tavily_key,
            fallback_base_url=rnd.llm.base_url,
            fallback_api_key=rnd.llm.api_key,
            meta_out=meta_out,
        )
    except Exception:  # noqa: BLE001 —— 搜索失败走「没找到」/自答口径
        return []


async def _web_precheck(rnd: _ChatRound, *, meta_out: dict | None = None) -> list[dict[str, str]]:
    """决策树前置联网预检(原 3 处同形调用点合一:chat 前置 / 流式流前 / agent 回落流内)。

    库内没命中 OR 命中但不相关(正文不含查询关键词)→ 联网搜索;但用户挂了图时
    图本身就是内容(问的常是「这图讲什么」),搜「图片/内容」只会搜出 OCR 工具
    之类——所以挂图不走联网,让视觉模型直接看图回答。
    """
    kb_irrelevant = not rnd.kb_hits or not _has_relevant_hit(rnd.kb_hits, rnd.search_query)
    if not (kb_irrelevant and rnd.tavily_key and not rnd.user_images):
        return []
    return await _search_web_safe(rnd, meta_out=meta_out)


async def _answer_web_round(rnd: _ChatRound, web_results: list[dict[str, str]]) -> QaResult:
    """决策树联网回答(原 4 处 answer_web 同参调用点合一)。"""
    return await asyncio.to_thread(
        answer_web,
        rnd.payload.question,
        web_results,
        history=rnd.history,
        nickname=rnd.nickname,
        client=rnd.llm,
        ref_notes=rnd.ref_notes,
        attachments=rnd.attachments,
        digest_summaries=rnd.digest_summaries,
        images=rnd.user_images,
    )


_SELF_KNOWLEDGE_PREFIX = "（笔记库里没有相关内容，以下用 AI 自身的知识回答）\n\n"


def _self_knowledge_answer(body: str, *, has_user_images: bool) -> str:
    """[NO_KB] 无联网兜底(原 2 处同文案合一):挂图直接用 body(图是内容,不加前缀);
    否则加「笔记库没有,以下用 AI 自身知识」明示前缀(防瞎编纪律:不冒充库内答案)。"""
    return body if has_user_images else _SELF_KNOWLEDGE_PREFIX + body


def _agent_result_fields(rnd: _ChatRound, agent_qa: QaResult) -> dict:
    """agent 答案字段抽取(两端点同款):used_web/trace 从 meta,计费从 QaResult。"""
    agent_meta = agent_qa.meta.get("agent") or {}
    return {
        "answer_text": agent_qa.answer,
        "citations": agent_qa.citations,
        "cost_tokens": agent_qa.cost_tokens or 0,
        "model_name": agent_qa.model or rnd.llm.model,
        "cache_hit": agent_qa.prompt_cache_hit_tokens or 0,
        "cache_miss": agent_qa.prompt_cache_miss_tokens or 0,
        "trace_steps": agent_meta.get("steps"),
        "used_web": bool(agent_meta.get("used_web")),
    }


async def _kb_answer_full(rnd: _ChatRound, *, images: list[dict]) -> AsyncIterator[dict]:
    """非流式库内回答机制:answer() 一次拿全文([NO_KB] 整体判定,无增量 delta 事件)。"""
    qa: QaResult = await asyncio.to_thread(
        answer,
        rnd.payload.question,
        rnd.kb_hits,
        history=rnd.history,
        nickname=rnd.nickname,
        client=rnd.llm,
        ref_notes=rnd.ref_notes,
        attachments=rnd.attachments,
        digest_summaries=rnd.digest_summaries,
        images=images,
    )
    marker = qa.answer.startswith("[NO_KB]")
    body = qa.answer.removeprefix("[NO_KB]").strip()
    yield {
        "type": "kb_done",
        "marker": marker,
        "text": body if marker else qa.answer,
        "cost_tokens": qa.cost_tokens or 0,
        "model_name": qa.model,
        "cache_hit": qa.prompt_cache_hit_tokens or 0,
        "cache_miss": qa.prompt_cache_miss_tokens or 0,
    }


async def _stream_kb_answer(rnd: _ChatRound) -> AsyncIterator[dict]:
    """流式库内回答机制:llm.chat_stream 逐段推进放线程里 + [NO_KB] 首行门
    (缓冲到能判定首行再放行,标记答案整体扣下不外泄);usage 事件攒计费。

    终事件 kb_done:marker=True 时 text=去掉标记的 body,否则 text=放行全文
    (短回答没到放行阈值,流结束后整段放行);计费取 usage 事件。"""
    accumulated: list[str] = []
    usage_event: dict | None = None
    iterator = rnd.llm.chat_stream(rnd.messages, max_tokens=3000)
    marker = False  # [NO_KB] = 笔记答不了,LLM 会给自身知识回答(不外发)
    buffered = ""
    streaming = False
    try:
        while True:
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
                yield {"type": "delta", "text": piece}
                continue
            buffered += piece
            combined = buffered.lstrip()
            if combined.startswith("[NO_KB]"):
                marker = True
                continue
            # 缓冲到能判定首行再放行,防止标记漏给用户
            if "\n" in buffered or (len(combined) >= 8 and not combined.startswith("[")):
                streaming = True
                accumulated.append(buffered)
                yield {"type": "delta", "text": buffered}
                buffered = ""
            # else: 继续缓冲直到可判定
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()

    if not marker and buffered:  # 短回答没到放行阈值,流结束后整段放行
        accumulated.append(buffered)
        yield {"type": "delta", "text": buffered}
    yield {
        "type": "kb_done",
        "marker": marker,
        "text": (
            buffered.removeprefix("[NO_KB]").strip()
            if marker
            else "".join(accumulated).strip() or _EMPTY_ANSWER
        ),
        "cost_tokens": (usage_event or {}).get("total_tokens") or 0,
        "model_name": rnd.llm.model,
        "cache_hit": (usage_event or {}).get("prompt_cache_hit_tokens") or 0,
        "cache_miss": (usage_event or {}).get("prompt_cache_miss_tokens") or 0,
    }


async def _decision_tree_answer(
    rnd: _ChatRound,
    *,
    web_results: list[dict[str, str]],
    kb_answer: AsyncIterator[dict],
) -> AsyncIterator[dict]:
    """决策树回答段共享实现(原 chat/chat_stream 各一份,现两端点消费同一实现)。

    事件序:``status*``(联网降级留痕)→ ``delta*``(答案增量)→ ``result``(终事件,
    必有且唯一)。流式端点把事件映射成 SSE;非流式端点丢弃 status/delta(无流可发),
    只取 result——两端对外行为零变化。

    ``web_results``:调用方已算好的前置联网结果(流式在流开始前算好——meta 事件要
    报 used_web;非流式在存 user 消息前算好;agent 回落时由流式端点在流内现算)。
    ``kb_answer``:库内回答机制(非流式 _kb_answer_full / 流式 _stream_kb_answer),
    产出 delta 增量事件 + 终事件 kb_done(marker/body/计费字段)。
    """
    if web_results:
        # 联网兜底走非流式回答(web 上下文结构不同),整段作为一帧 delta 下发
        qa = await _answer_web_round(rnd, web_results)
        yield {"type": "delta", "text": qa.answer}
        yield {
            "type": "result",
            "answer_text": qa.answer,
            "citations": build_web_citations(web_results, qa.answer),
            "cost_tokens": qa.cost_tokens or 0,
            "model_name": qa.model or rnd.llm.model,
            "cache_hit": qa.prompt_cache_hit_tokens or 0,
            "cache_miss": qa.prompt_cache_miss_tokens or 0,
            "used_web": True,
        }
        return

    marker = False
    kb: dict = {}
    async for ev in kb_answer:
        if ev["type"] == "delta":
            yield ev  # 库内回答增量直通(流式机制才产 delta;非流式机制无增量)
        else:
            kb = ev
            marker = ev["marker"]
    if not marker:
        yield {
            "type": "result",
            "answer_text": kb["text"],
            "citations": build_citations(rnd.kb_hits, kb["text"]),
            "cost_tokens": kb["cost_tokens"],
            "model_name": kb["model_name"],
            "cache_hit": kb["cache_hit"],
            "cache_miss": kb["cache_miss"],
            "used_web": False,
        }
        return
    # 二段兜底:LLM 用 [NO_KB] 标记「笔记答不了」→ 联网搜;没配 Tavily 就用 LLM 已
    # 给出的自身知识正面回答(带明示前缀)。相关性检查只认字面(「数据库」会命中发票
    # 笔记),语义层面的「答不了」由 LLM 自己判。挂了图时不走联网(图本身是内容)。
    web_results2: list[dict[str, str]] = []
    if rnd.tavily_key and not rnd.user_images:
        fb_meta: dict = {}  # 两级链降级留痕(见 websearch.search_web)
        web_results2 = await _search_web_safe(rnd, meta_out=fb_meta)
        if fb_meta.get("fallback"):
            yield {"type": "status", "text": f"联网搜索降级:{fb_meta['fallback']}"}
    if web_results2:
        qa = await _answer_web_round(rnd, web_results2)
        yield {"type": "delta", "text": qa.answer}
        yield {
            "type": "result",
            "answer_text": qa.answer,
            "citations": build_web_citations(web_results2, qa.answer),
            "cost_tokens": qa.cost_tokens or 0,
            "model_name": qa.model or rnd.llm.model,
            "cache_hit": qa.prompt_cache_hit_tokens or 0,
            "cache_miss": qa.prompt_cache_miss_tokens or 0,
            "used_web": True,
        }
    else:
        answer_text = _self_knowledge_answer(kb["text"], has_user_images=bool(rnd.user_images))
        yield {"type": "delta", "text": answer_text}
        yield {
            "type": "result",
            "answer_text": answer_text,
            "citations": [],
            "cost_tokens": kb["cost_tokens"],
            "model_name": kb["model_name"],
            "cache_hit": kb["cache_hit"],
            "cache_miss": kb["cache_miss"],
            "used_web": False,
        }


async def _persist_assistant_round(
    rnd: _ChatRound, session: AsyncSession, *, fields: dict
) -> QaMessage:
    """存 assistant 消息(出处+计费+缓存计数+agent 留痕)+ commit + 冻结分段摘要
    (近窗超阈值则异步冻结成 digest,fire-and-forget;失败静默——digest 是
    best-effort,不能因入队失败影响已完成的回答)。两端点同款。"""
    assistant_msg = await rnd.repo.add_message(
        rnd.qa_session.id,
        role="assistant",
        content=fields["answer_text"],
        citations=[c.model_dump() for c in fields["citations"]],
        model=fields["model_name"],
        cost_tokens=fields["cost_tokens"],
        cache_hit_tokens=fields["cache_hit"],
        cache_miss_tokens=fields["cache_miss"],
        trace=fields.get("trace_steps"),
    )
    await session.commit()
    if sum(estimate_tokens(m["content"]) for m in rnd.history) > DIGEST_TRIGGER_TOKENS:
        freeze = rnd.window_msgs[:DIGEST_FREEZE_BATCH]
        try:
            from app.worker.worker import enqueue_digest

            await enqueue_digest(
                rnd.qa_session.id,
                owner_id=rnd.owner_id,
                start_msg_id=freeze[0].id,
                end_msg_id=freeze[-1].id,
            )
        except Exception:  # noqa: BLE001 —— Redis/入队失败不影响已完成的回答
            pass
    return assistant_msg


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    retrieve: RetrieveDep,
    llm: LLMClientDep,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
) -> ChatResponse:
    started = time.perf_counter()
    rnd = await _prepare_chat_round(
        payload, retrieve=retrieve, llm=llm, session=session, owner_id=owner_id
    )

    # 4.4) 问答 agent 灰度(ADR 0010):开关开 → 脑在 loop 里选工具(换词再搜/何时转联网),
    #   入口契约/预算三道闸/出口校验在马具;脑没给出答案(预算耗尽/LLM 失败/空答案)→
    #   返回 None,回落下面 4.5/5 的现有决策树——最坏情况 = 现状。
    agent_qa: QaResult | None = None
    if rnd.agent_enabled:
        agent_qa = await run_agent(
            payload.question,
            rnd.kb_hits,
            retrieve=retrieve,
            client=llm,
            owner_id=owner_id,
            top_k=payload.top_k,
            history=rnd.history,
            nickname=rnd.nickname,
            ref_notes=rnd.ref_notes,
            attachments=rnd.attachments,
            digest_summaries=rnd.digest_summaries,
            images=rnd.user_images + await _load_hit_images(session, owner_id, rnd.kb_hits),
            has_user_images=bool(rnd.user_images),
            tavily_key=rnd.tavily_key,
            fallback_base_url=llm.base_url,
            fallback_api_key=llm.api_key,
        )

    # 4.5) 工具链兜底(决策树,agent 关/agent 回落时走):前置联网预检(共享 _web_precheck)
    web_results: list[dict[str, str]] = []
    if agent_qa is None:
        web_results = await _web_precheck(rnd)

    # 4) 存 user 消息(两份:content 给人看,llm_content 给下轮 LLM 重放)
    user_msg = await rnd.repo.add_message(
        rnd.qa_session.id, role="user", content=payload.question, llm_content=rnd.llm_user_text
    )

    # 5) 回答:agent 开且成功 → agent 答案;否则共享决策树(联网/库内/[NO_KB] 二段兜底)。
    #    非流式端点丢弃决策树事件(无流可发),只取终事件 result。
    fields: dict = {}
    if agent_qa is not None:
        fields = _agent_result_fields(rnd, agent_qa)
    else:
        # 命中图片笔记 → 回传原图给视觉模型(联网路径不附图,上下文来自搜索结果)
        hit_images = [] if web_results else await _load_hit_images(session, owner_id, rnd.kb_hits)
        async for ev in _decision_tree_answer(
            rnd,
            web_results=web_results,
            kb_answer=_kb_answer_full(rnd, images=rnd.user_images + hit_images),
        ):
            if ev["type"] == "result":
                fields = ev

    # 5) 存 assistant 消息 + 整体 commit(失败全回滚)+ 冻结分段摘要(共享 _persist_assistant_round)
    assistant_msg = await _persist_assistant_round(rnd, session, fields=fields)

    took_ms = int(round((time.perf_counter() - started) * 1000))
    return ChatResponse(
        session_id=rnd.qa_session.id,
        session_title=rnd.qa_session.title,
        user_message=_message_out(user_msg),
        assistant_message=_message_out(assistant_msg),
        search_query=rnd.search_query,
        used_web=fields["used_web"],
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
    """流式问答:SSE 事件流 meta → status* → delta* → done(error 兜底)。

    改写与检索在流开始**之前**完成(客户端等待期间显示「思考中」);
    轮次开始即 commit 用户消息(回答失败它也不丢),回答完成再 commit 助手消息。
    决策树回答段与非流式端点共享 ``_decision_tree_answer``,事件包装是唯一差异。
    """
    started = time.perf_counter()
    rnd = await _prepare_chat_round(
        payload, retrieve=retrieve, llm=llm, session=session, owner_id=owner_id
    )

    # 决策树前置(agent 关时):流开始**前**算好 used_web/hit_images/附图——meta 事件要报。
    # agent 开时:命中图预加载(meta 计数 + 回落时 attach 复用),meta 时点 used_web
    # 未知(真值随 done 的 citations;与 marker 翻转同款已存行为)。
    web_results: list[dict[str, str]] = []
    if rnd.agent_enabled:
        hit_images = await _load_hit_images(session, owner_id, rnd.kb_hits)
        used_web = False
    else:
        web_results = await _web_precheck(rnd)
        used_web = bool(web_results)
        # 命中图片笔记 → 回传原图给视觉模型(联网路径不附图);须在 gen() 闭包捕获 messages 前完成
        hit_images = [] if used_web else await _load_hit_images(session, owner_id, rnd.kb_hits)
        attach_images(rnd.messages, rnd.user_images + hit_images)

    user_msg = await rnd.repo.add_message(
        rnd.qa_session.id, role="user", content=payload.question, llm_content=rnd.llm_user_text
    )
    await session.commit()  # 轮次开始落库;回答完成后再 commit 助手消息
    user_out = _message_out(user_msg)

    async def gen():
        nonlocal web_results  # agent 回落在流内现算决策树前置时改写
        try:
            yield _sse(
                "meta",
                {
                    "session_id": rnd.qa_session.id,
                    "session_title": rnd.qa_session.title,
                    "search_query": rnd.search_query,
                    "used_web": used_web,
                    # 本轮意图是否 agent loop(ADR 0010;agent 模式下真值随 done 事件,
                    # meta 时点尚未发生——used_web/出处以 done 的 citations 为准)
                    "agent": rnd.agent_enabled,
                    "images_attached": len(hit_images) + len(rnd.user_images),  # 命中图+附件图
                    "model": llm.model,  # 本次回答用的模型,前端状态行显示
                    # 检索/改写花了多久(秒):前端据此区分「在检索」还是「在等模型」
                    "prepare_ms": int(round((time.perf_counter() - started) * 1000)),
                    "user_message": user_out.model_dump(),
                },
            )
            agent_qa: QaResult | None = None
            if rnd.agent_enabled:
                # agent 路径:loop 在流内跑——最终答案边生成边放行(流式,主人验收反馈),
                # 工具轮发 status;[NO_KB] 标记由首行门扣下、兜底文本整段补发
                async for ev in run_agent_iter(
                    payload.question,
                    rnd.kb_hits,
                    retrieve=retrieve,
                    client=llm,
                    owner_id=owner_id,
                    top_k=payload.top_k,
                    history=rnd.history,
                    nickname=rnd.nickname,
                    ref_notes=rnd.ref_notes,
                    attachments=rnd.attachments,
                    digest_summaries=rnd.digest_summaries,
                    images=rnd.user_images + hit_images,
                    has_user_images=bool(rnd.user_images),
                    tavily_key=rnd.tavily_key,
                    fallback_base_url=llm.base_url,
                    fallback_api_key=llm.api_key,
                    streaming=True,
                ):
                    if ev["type"] == "status":
                        yield _sse("status", {"text": ev["text"]})
                    elif ev["type"] == "delta":
                        yield _sse("delta", {"text": ev["text"]})
                    elif ev["type"] == "result":
                        agent_qa = ev["qa"]
            fields: dict = {}
            if agent_qa is not None:
                fields = _agent_result_fields(rnd, agent_qa)
            else:
                if rnd.agent_enabled:
                    # agent 回落(预算耗尽/LLM 失败):gen 内现算决策树前置(预检 + 附图),
                    # 结果写进 web_results —— 共享决策树照常消费,最坏情况 = 现状
                    fb_meta: dict = {}  # 两级链降级留痕(见 websearch.search_web)
                    web_results = await _web_precheck(rnd, meta_out=fb_meta)
                    if fb_meta.get("fallback"):
                        yield _sse("status", {"text": f"联网搜索降级:{fb_meta['fallback']}"})
                    attach_images(rnd.messages, rnd.user_images + hit_images)
                # 共享决策树回答段:status(降级留痕)/delta(答案增量)映射成 SSE,取终事件 result
                async for ev in _decision_tree_answer(
                    rnd,
                    web_results=web_results,
                    kb_answer=_stream_kb_answer(rnd),
                ):
                    if ev["type"] == "status":
                        yield _sse("status", {"text": ev["text"]})
                    elif ev["type"] == "delta":
                        yield _sse("delta", {"text": ev["text"]})
                    elif ev["type"] == "result":
                        fields = ev

            assistant_msg = await _persist_assistant_round(rnd, session, fields=fields)
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
async def delete_session(session_id: int, session: SessionDep, owner_id: CurrentOwnerDep) -> None:
    deleted = await QaRepo(session, owner_id).delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    await session.commit()


@router.patch("/sessions/{session_id}", response_model=SessionItem)
async def rename_session(
    session_id: int, body: RenameSessionRequest, session: SessionDep, owner_id: CurrentOwnerDep
) -> SessionItem:
    """重命名会话。updated_at 语义是「最近活跃」——重命名不算活跃不刷它,列表位次不动。"""
    qa_session = await QaRepo(session, owner_id).rename_session(session_id, body.title)
    if qa_session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在:{session_id}")
    await session.commit()
    return SessionItem(id=qa_session.id, title=qa_session.title, created_at=qa_session.created_at)


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


async def _organize_and_enqueue(note_title: str, note_md: str, owner_id: int) -> SaveNoteItem:
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
    # citations 要跟着进整理流程:[W1]/[1] 只是各条回答的本地编号,合并成一篇会串号,
    # 由 note_dialogue 统一重编后再交给模型,文末按编号补真实地址。
    plain = [
        {"role": m.role, "content": m.content, "citations": list(m.citations or [])}
        for m in messages
    ]
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
            topics_for_notes = [t for i, t in enumerate(plan.topics) if i in picked] or list(
                plan.topics
            )
        else:
            topics_for_notes = list(plan.topics)

    items: list[SaveNoteItem] = []
    if not topics_for_notes:
        dialogue, citations = note_dialogue(plain)
        note_md = await organize_session_note(
            qa_session.title, dialogue, instruction=instruction, client=llm
        )
        note_md = append_citation_appendix(note_md, citations)
        items.append(await _organize_and_enqueue(qa_session.title, note_md, owner_id))
    else:
        # 每主题一篇,串行(D3 口径);区间外的闲聊不带进单篇,保持笔记聚焦
        for topic in topics_for_notes:
            dialogue, citations = note_dialogue(plain, topic.msg_start, topic.msg_end)
            note_title = topic.title if topic.title else qa_session.title
            note_md = await organize_session_note(
                note_title, dialogue, instruction=instruction, client=llm
            )
            note_md = append_citation_appendix(note_md, citations)
            items.append(await _organize_and_enqueue(note_title, note_md, owner_id))

    first = items[0]
    return SaveNoteResponse(
        note_file=first.note_file,
        organized_chars=first.organized_chars,
        import_enqueued=first.import_enqueued,
        import_job_id=first.import_job_id,
        notes=items,
    )
