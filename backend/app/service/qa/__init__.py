"""问答服务:检索命中 → LLM → 带出处的答案(无状态)。"""

from app.service.qa.answerer import ContextHit, QaCitation, QaResult, answer, build_qa_messages

__all__ = ["ContextHit", "QaCitation", "QaResult", "answer", "build_qa_messages"]
