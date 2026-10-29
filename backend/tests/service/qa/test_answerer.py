"""问答 answerer 单测:全注入 fake Transport,**绝不真调 LLM**。

覆盖:prompt 组装([n] 编号 + <context> 包裹 + 问题)、命中为空短路(不调 LLM)、
正常答 + citations 映射、LLM 认证错误向上抛(不静默)。
"""

from __future__ import annotations

import json

import pytest

from app.service.llm import LLMAuthError, LLMClient
from app.service.qa.answerer import ContextHit, answer, build_qa_messages


class FakeTransport:
    """按顺序返回预置响应;异常项直接抛出。"""

    def __init__(self, responses: list[tuple | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "body": body})
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, raw = item
        return status, {}, raw


def _chat(content: str, *, total: int = 15) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": total},
        }
    ).encode()


def _client(responses) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-qa-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def _hit(
    *,
    note_id: int = 1,
    title: str = "闭包笔记",
    hp: str | None = "一、闭包 > 定义",
    parent: str = "闭包是函数 + 它捕获的外层变量。",
    child: str = "闭包是函数 + 它捕获的外层变量。",
) -> ContextHit:
    return ContextHit(
        note_id=note_id,
        note_title=title,
        heading_path=hp,
        parent_text=parent,
        child_text=child,
    )


class TestPromptAssembly:
    def test_messages_number_hits_and_wrap_context(self) -> None:
        hits = [_hit(note_id=10), _hit(note_id=20, title="Fetch 指南", hp="七、取消请求")]
        msgs, meta = build_qa_messages("闭包是什么", hits)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        user = msgs[1]["content"]
        assert "<context>" in user and "</context>" in user
        assert "[1]" in user and "[2]" in user
        assert "闭包笔记" in user and "Fetch 指南" in user
        assert "闭包是什么" in user
        assert meta["n_hits"] == 2

    def test_empty_hits_still_builds_messages(self) -> None:
        msgs, meta = build_qa_messages("x", [])
        assert "<context>" in msgs[1]["content"]
        assert meta["n_hits"] == 0


class TestAnswer:
    def test_empty_hits_short_circuits_no_llm_call(self) -> None:
        client, transport = _client([(200, _chat("不该被调用"))])
        result = answer("任意问题", [], client=client)
        assert result.answer == "笔记库里没有找到相关内容。"
        assert result.citations == []
        assert result.cost_tokens == 0
        assert transport.calls == []  # 没调 LLM

    def test_answer_with_hits_maps_citations(self) -> None:
        hits = [
            _hit(note_id=10),
            _hit(note_id=20, title="Fetch 指南", child="用 AbortController 取消"),
        ]
        content = "闭包是函数+捕获的外层变量。[1]\n取消请求用 AbortController。[2]"
        client, transport = _client([(200, _chat(content, total=42))])
        result = answer("闭包和取消请求", hits, client=client)
        assert "闭包" in result.answer and "[1]" in result.answer
        assert len(result.citations) == 2
        assert result.citations[0].n == 1 and result.citations[0].note_id == 10
        assert result.citations[1].n == 2 and result.citations[1].note_id == 20
        assert result.citations[0].snippet.startswith("闭包是函数")
        assert result.cost_tokens == 42
        assert result.model == "fake-model"
        assert transport.calls and transport.calls[0]["method"] == "POST"

    def test_empty_content_falls_back_to_not_found(self) -> None:
        """reasoning 模型偶发 content 为空时,兜底成「库里没有」,不返回空答案。"""
        client, _ = _client([(200, _chat("", total=8))])
        result = answer("问题", [_hit()], client=client)
        assert result.answer == "笔记库里没有找到相关内容。"

    def test_llm_auth_error_propagates_not_silenced(self) -> None:
        client, _ = _client([(401, b'{"error":{"message":"unauthorized"}}')])
        with pytest.raises(LLMAuthError):
            answer("问题", [_hit()], client=client)
