"""问答 API:POST /api/qa —— 用户提问 → 检索 top-k 命中 → LLM 答 + 出处。

复用线 K 的 ``retrieve``(延迟导入,不在启动加载 torch)和线 H-批1 的 ``LLMClient``。
无状态:不读不写库(除检索的只读 select);同步 LLM 调用用 ``asyncio.to_thread`` 包装,
不阻塞事件循环。LLM 异常(认证/网络/限流)向上抛 —— 与 G2 同口径,不静默降级。
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.api.library import OWNER_ID, RetrieveDep
from app.service.llm import LLMClient
from app.service.qa.answerer import ContextHit, QaResult, answer

router = APIRouter(prefix="/api", tags=["qa"])

MAX_QUERY_CHARS = 500
DEFAULT_TOP_K = 5
MAX_TOP_K = 20


class QaRequest(BaseModel):
    """POST /api/qa 请求体:question 1–500 字,top_k 1–20 默认 5。"""

    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)

    @field_validator("question")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question 不能为空白")
        return stripped


class QaCitationOut(BaseModel):
    n: int
    note_id: int
    note_title: str
    heading_path: str | None
    snippet: str


class QaResponse(BaseModel):
    question: str
    answer: str
    citations: list[QaCitationOut]
    took_ms: int
    cost_tokens: int | None
    model: str


def get_llm_client() -> LLMClient:
    """LLM client 工厂;测试用 ``dependency_overrides`` 换成带 FakeTransport 的假 client。"""
    return LLMClient.from_settings()


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]


@router.post("/qa", response_model=QaResponse)
async def ask(payload: QaRequest, retrieve: RetrieveDep, llm: LLMClientDep) -> QaResponse:
    started = time.perf_counter()
    # 1) 检索 top-k(线 K:双路召回 + RRF + small-to-big,命中带正文与 heading_path)
    result = await retrieve(payload.question, top_k=payload.top_k, owner_id=OWNER_ID)
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
    # 2) LLM 答(同步 client → to_thread,不阻塞事件循环)
    qa: QaResult = await asyncio.to_thread(answer, payload.question, hits, client=llm)
    took_ms = int(round((time.perf_counter() - started) * 1000))
    return QaResponse(
        question=qa.question,
        answer=qa.answer,
        citations=[QaCitationOut(**c.model_dump()) for c in qa.citations],
        took_ms=took_ms,
        cost_tokens=qa.cost_tokens,
        model=qa.model,
    )
