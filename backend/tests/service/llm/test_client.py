"""LLM adapter 单测:**绝不真调 LLM**,全部注入 fake transport。

覆盖:请求形状 / 错误分类 / 重试次数 / 响应解析 / probe 能力标记 / key 不入异常文本。
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from app.core.config import Settings
from app.service.llm import (
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)


class FakeTransport:
    """按顺序返回预置响应;异常项直接抛出。"""

    def __init__(self, responses: list[tuple[int, bytes] | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout}
        )
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _chat_body(content: str = "ok") -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    ).encode()


def _client(responses, **kwargs) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-unit-test-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
        **kwargs,
    )
    return client, transport


class TestChatSuccess:
    def test_request_shape_and_usage(self):
        client, transport = _client([(200, _chat_body("hello"))])
        result = client.chat([{"role": "user", "content": "hi"}], max_tokens=123)

        assert result.content == "hello"
        assert result.model == "fake-model"
        assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (11, 7, 18)

        call = transport.calls[0]
        assert call["url"] == "https://api.example.test/v1/chat/completions"
        assert call["method"] == "POST"
        assert call["headers"]["Authorization"] == "Bearer sk-unit-test-SECRET"
        sent = json.loads(call["body"])
        assert sent["model"] == "fake-model"
        assert sent["max_tokens"] == 123
        assert sent["messages"] == [{"role": "user", "content": "hi"}]
        assert "response_format" not in sent

    def test_response_format_passthrough(self):
        client, transport = _client([(200, _chat_body("{}"))])
        client.chat([{"role": "user", "content": "x"}], response_format={"type": "json_object"})
        assert json.loads(transport.calls[0]["body"])["response_format"] == {"type": "json_object"}


class TestErrorClassification:
    def test_auth_failure_raises_without_leaking_key(self):
        client, transport = _client([(401, b'{"error":"invalid key"}')])
        with pytest.raises(LLMAuthError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 401
        assert "sk-unit-test-SECRET" not in str(ei.value)
        assert len(transport.calls) == 1  # 认证失败不重试

    def test_rate_limit_not_retried(self):
        client, transport = _client([(429, b"{}")])
        with pytest.raises(LLMRateLimitError):
            client.chat([{"role": "user", "content": "x"}])
        assert len(transport.calls) == 1

    def test_other_4xx_not_retried(self):
        client, transport = _client([(400, b'{"error":"bad request"}')])
        with pytest.raises(LLMResponseError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 400
        assert len(transport.calls) == 1

    def test_timeout_retried_twice_then_raises(self):
        timeouts = [TimeoutError("timed out")] * 3
        client, transport = _client(timeouts)
        with pytest.raises(LLMTimeoutError):
            client.chat([{"role": "user", "content": "x"}])
        assert len(transport.calls) == 3  # 1 次 + 重试 2 次

    def test_network_error_classified(self):
        client, _ = _client([urllib.error.URLError("connection refused")] * 3)
        with pytest.raises(LLMNetworkError):
            client.chat([{"role": "user", "content": "x"}])

    def test_5xx_retried_then_success(self):
        client, transport = _client([(500, b"boom"), (200, _chat_body("recovered"))])
        assert client.chat([{"role": "user", "content": "x"}]).content == "recovered"
        assert len(transport.calls) == 2

    def test_5xx_exhausted(self):
        client, transport = _client([(503, b"")] * 3)
        with pytest.raises(LLMResponseError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 503
        assert len(transport.calls) == 3

    def test_malformed_body(self):
        client, _ = _client([(200, b"not json")])
        with pytest.raises(LLMResponseError):
            client.chat([{"role": "user", "content": "x"}])

    def test_missing_choices(self):
        client, _ = _client([(200, b'{"model":"fake-model"}')])
        with pytest.raises(LLMResponseError):
            client.chat([{"role": "user", "content": "x"}])


class TestProbe:
    def test_models_and_json_mode_available(self):
        client, transport = _client([(200, b'{"data":[]}'), (200, _chat_body("{}"))])
        caps = client.probe()
        assert caps.models_endpoint is True
        assert caps.chat is True
        assert caps.json_mode is True
        assert caps.model == "fake-model"
        assert transport.calls[0]["method"] == "GET"
        assert transport.calls[0]["url"] == "https://api.example.test/v1/models"

    def test_models_endpoint_absent_falls_back_to_chat(self):
        client, _ = _client([(404, b"{}"), (200, _chat_body("{}"))])
        caps = client.probe()
        assert caps.models_endpoint is False
        assert caps.chat is True
        assert caps.json_mode is True
        assert caps.notes  # 记录了回退原因

    def test_json_mode_rejected_then_retried_without_it(self):
        client, transport = _client(
            [
                (200, b"{}"),
                (400, b'{"error":"response_format unsupported"}'),
                (200, _chat_body("{}")),
            ]
        )
        caps = client.probe()
        assert caps.json_mode is False
        assert caps.chat is True
        assert len(transport.calls) == 3
        assert "response_format" not in json.loads(transport.calls[2]["body"])

    def test_auth_failure_propagates(self):
        client, transport = _client([(401, b"{}")])
        with pytest.raises(LLMAuthError):
            client.probe()
        assert len(transport.calls) == 1  # 后续 chat 探测不再进行


class TestConstruction:
    def test_missing_key_raises(self):
        with pytest.raises(LLMAuthError):
            LLMClient(base_url="https://x/v1", api_key="", model="m")

    def test_empty_messages_rejected(self):
        client, transport = _client([(200, _chat_body())])
        with pytest.raises(LLMError):
            client.chat([])
        assert transport.calls == []

    def test_from_settings(self):
        settings = Settings(
            llm_base_url="https://api.other.test/v1",
            llm_api_key="sk-from-settings",
            llm_model="other-model",
        )
        client = LLMClient.from_settings(settings)
        assert client.base_url == "https://api.other.test/v1/"
        assert client.model == "other-model"
