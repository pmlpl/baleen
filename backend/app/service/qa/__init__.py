"""问答服务:检索命中 → LLM → 带出处的答案;追问先改写再检索。"""

from app.service.qa.answerer import ContextHit, QaCitation, QaResult, answer, build_qa_messages
from app.service.qa.rewriter import rewrite_question

__all__ = [
    "ContextHit",
    "QaCitation",
    "QaResult",
    "answer",
    "build_qa_messages",
    "rewrite_question",
]
