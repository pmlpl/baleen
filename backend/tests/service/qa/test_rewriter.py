"""问题改写器单测:FakeTransport 不联网。

覆盖:改写返回(剥引号/前缀/取首行)、prompt 含历史与原问题、无历史不调 LLM、
LLM 异常向上抛(调用方兜底回退——口径在 handler 层测)。
"""

from __future__ import annotations

import json

import pytest

from app.service.llm import LLMAuthError, LLMClient
from app.service.qa.rewriter import rewrite_question


class FakeTransport:
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


def _chat(content: str) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
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


def _history() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "什么是 Promise，怎么手写"},
        {"role": "assistant", "content": "Promise 是异步操作的结果对象,手写要点如下……"},
    ]


class TestRewrite:
    def test_returns_rewritten_question(self) -> None:
        client, transport = _client([(200, _chat("手写实现的 Promise 有哪几种状态"))])
        q = rewrite_question("它有哪几种状态", _history(), client=client)
        assert q == "手写实现的 Promise 有哪几种状态"
        # prompt 里带对话历史与原问题
        body = json.loads(transport.calls[0]["body"])
        user = body["messages"][-1]["content"]
        assert "什么是 Promise" in user
        assert "它有哪几种状态" in user

    def test_strips_quotes_and_rewrite_prefix(self) -> None:
        client, _ = _client([(200, _chat("改写后的问题：手写 Promise 的三种状态"))])
        q = rewrite_question("它的状态", _history(), client=client)
        assert q == "手写 Promise 的三种状态"

    def test_multiline_output_takes_first_line(self) -> None:
        client, _ = _client([(200, _chat("手写 Promise 的三种状态\n解释:因为……"))])
        q = rewrite_question("它的状态", _history(), client=client)
        assert q == "手写 Promise 的三种状态"

    def test_no_history_returns_original_without_llm(self) -> None:
        client, transport = _client([(200, _chat("不该被调用"))])
        q = rewrite_question("独立的完整问题", [], client=client)
        assert q == "独立的完整问题"
        assert transport.calls == []  # 首轮不改写,不花 LLM 钱

    def test_llm_error_propagates(self) -> None:
        """改写失败向上抛,由 handler 兜底回退原问题(这里只测不静默吞)。"""
        client, _ = _client([(401, b'{"error":{"message":"unauthorized"}}')])
        with pytest.raises(LLMAuthError):
            rewrite_question("它有哪几种状态", _history(), client=client)
