"""LLM adapter 单测:**绝不真调 LLM**,全部注入 fake transport。

覆盖:请求形状 / 错误分类 / 重试次数 / 响应解析 / probe 能力标记 / key 不入异常文本。
"""

from __future__ import annotations

import http.client
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
from app.service.llm.client import _short


class FakeTransport:
    """按顺序返回预置响应;异常项直接抛出。

    响应项可写成 2 元组 ``(status, body)``(无响应头,兼容旧写法),
    或 3 元组 ``(status, headers, body)``(测 Retry-After 等响应头)。
    """

    def __init__(self, responses: list[tuple | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout}
        )
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if len(item) == 2:
            status, raw = item
            return status, {}, raw
        return item


def _chat_body(content: str = "ok") -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    ).encode()


def _client(
    responses, *, sleeps: list[float] | None = None, **kwargs
) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-unit-test-SECRET",
        model="fake-model",
        transport=transport,
        sleep=sleeps.append if sleeps is not None else (lambda _s: None),
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

    def test_finish_reason_length_is_surfaced(self):
        """截断必须能从 ChatResult 看出来,否则长文生成会被静默当成正常结束。"""
        body = json.dumps(
            {
                "model": "fake-model",
                "choices": [
                    {"message": {"role": "assistant", "content": "半截"}, "finish_reason": "length"}
                ],
                "usage": {"total_tokens": 18},
            }
        ).encode()
        client, _ = _client([(200, body)])
        result = client.chat([{"role": "user", "content": "hi"}])
        assert result.finish_reason == "length"
        assert result.content == "半截"

    def test_finish_reason_missing_is_none(self):
        """上游不给 finish_reason 时按 None,不能崩。"""
        client, _ = _client([(200, _chat_body("hello"))])
        result = client.chat([{"role": "user", "content": "hi"}])
        assert result.finish_reason is None

    def test_response_format_passthrough(self):
        client, transport = _client([(200, _chat_body("{}"))])
        client.chat([{"role": "user", "content": "x"}], response_format={"type": "json_object"})
        assert json.loads(transport.calls[0]["body"])["response_format"] == {"type": "json_object"}


class TestFunctionCalling:
    """tools / tool_choice 透传 + tool_calls 解析(ADR 0010)。None = 与现状一致,老调用方零改动。"""

    def test_tools_and_tool_choice_passthrough(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_kb",
                    "description": "搜索笔记库",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]
        client, transport = _client([(200, _chat_body("ok"))])
        client.chat([{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto")
        sent = json.loads(transport.calls[0]["body"])
        assert sent["tools"] == tools
        assert sent["tool_choice"] == "auto"

    def test_no_tools_by_default_keeps_legacy_shape(self):
        """不传 tools → 请求体没有 tools/tool_choice 字段(与现状字节一致,保前缀缓存)。"""
        client, transport = _client([(200, _chat_body("ok"))])
        client.chat([{"role": "user", "content": "hi"}])
        sent = json.loads(transport.calls[0]["body"])
        assert "tools" not in sent
        assert "tool_choice" not in sent

    def test_tool_calls_parsed(self):
        body = json.dumps(
            {
                "model": "fake-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,  # 发起工具调用时 content 常为 null
                            "tool_calls": [
                                {
                                    "id": "call_0",
                                    "type": "function",
                                    "function": {
                                        "name": "search_kb",
                                        "arguments": '{"query": "hadoop 生态"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 30},
            }
        ).encode()
        client, _ = _client([(200, body)])
        result = client.chat([{"role": "user", "content": "hi"}], tools=[])
        assert result.tool_calls == [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "search_kb", "arguments": '{"query": "hadoop 生态"}'},
            }
        ]
        assert result.content == ""  # null → 空串,不炸
        assert result.finish_reason == "tool_calls"

    def test_no_tool_calls_returns_empty_list(self):
        client, _ = _client([(200, _chat_body("普通回答"))])
        result = client.chat([{"role": "user", "content": "hi"}])
        assert result.tool_calls == []

    def test_malformed_tool_call_entries_skipped(self):
        """缺 id / 缺 function.name 的坏条目跳过,不让单条坏数据炸整轮。"""
        body = json.dumps(
            {
                "model": "fake-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "答",
                            "tool_calls": [
                                "不是dict",
                                {"function": {"name": "no_id"}},  # 缺 id
                                {"id": "call_1", "function": {"name": ""}},  # 缺 name
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {"name": "search_web", "arguments": "{}"},
                                },
                            ],
                        }
                    }
                ],
                "usage": {"total_tokens": 30},
            }
        ).encode()
        client, _ = _client([(200, body)])
        result = client.chat([{"role": "user", "content": "hi"}], tools=[])
        assert [tc["id"] for tc in result.tool_calls] == ["call_2"]
        assert result.content == "答"

    def test_per_call_timeout_override(self):
        """timeout 覆盖传到 transport;默认 = 构造时的 self.timeout。"""
        client, transport = _client([(200, _chat_body("ok")), (200, _chat_body("ok"))])
        client.chat([{"role": "user", "content": "x"}], timeout=12.5)
        assert transport.calls[0]["timeout"] == 12.5
        client.chat([{"role": "user", "content": "x"}])
        assert transport.calls[1]["timeout"] == 60.0  # DEFAULT_TIMEOUT


class TestErrorClassification:
    def test_auth_failure_raises_without_leaking_key(self):
        client, transport = _client([(401, b'{"error":"invalid key"}')])
        with pytest.raises(LLMAuthError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 401
        assert "sk-unit-test-SECRET" not in str(ei.value)
        assert len(transport.calls) == 1  # 认证失败不重试

    def test_rate_limit_retries_with_retry_after(self):
        sleeps: list[float] = []
        client, transport = _client(
            [(429, {"Retry-After": "1"}, b"{}"), (200, {}, _chat_body("ok"))],
            sleeps=sleeps,
        )
        assert client.chat([{"role": "user", "content": "x"}]).content == "ok"
        assert len(transport.calls) == 2
        assert sleeps == [1.0]

    def test_rate_limit_without_retry_after_uses_backoff(self):
        sleeps: list[float] = []
        client, transport = _client(
            [(429, b"{}"), (429, b"{}"), (200, _chat_body("recovered"))],
            sleeps=sleeps,
        )
        assert client.chat([{"role": "user", "content": "x"}]).content == "recovered"
        assert len(transport.calls) == 3
        assert sleeps == [5.0, 15.0]

    def test_rate_limit_exhausted(self):
        sleeps: list[float] = []
        client, transport = _client([(429, b"{}")] * 3, sleeps=sleeps, max_retries=2)
        with pytest.raises(LLMRateLimitError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 429
        assert len(transport.calls) == 3  # 1 次 + 重试 2 次
        assert sleeps == [5.0, 15.0]

    def test_rate_limit_retry_after_capped(self):
        sleeps: list[float] = []
        client, _ = _client(
            [(429, {"Retry-After": "1000"}, b"{}"), (200, {}, _chat_body())],
            sleeps=sleeps,
        )
        client.chat([{"role": "user", "content": "x"}])
        assert sleeps == [90.0]  # 单次等待不超过 90s

    def test_other_4xx_not_retried(self):
        client, transport = _client([(400, b'{"error":"bad request"}')])
        with pytest.raises(LLMResponseError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 400
        assert len(transport.calls) == 1

    def test_timeout_retried_twice_then_raises(self):
        timeouts = [TimeoutError("timed out")] * 3
        client, transport = _client(timeouts, max_retries=2)
        with pytest.raises(LLMTimeoutError):
            client.chat([{"role": "user", "content": "x"}])
        assert len(transport.calls) == 3  # 1 次 + 重试 2 次

    def test_network_error_classified(self):
        client, _ = _client([urllib.error.URLError("connection refused")] * 3, max_retries=2)
        with pytest.raises(LLMNetworkError):
            client.chat([{"role": "user", "content": "x"}])

    def test_5xx_retried_then_success(self):
        client, transport = _client([(500, b"boom"), (200, _chat_body("recovered"))])
        assert client.chat([{"role": "user", "content": "x"}]).content == "recovered"
        assert len(transport.calls) == 2

    def test_5xx_exhausted(self):
        client, transport = _client([(503, b"")] * 3, max_retries=2)
        with pytest.raises(LLMResponseError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 503
        assert len(transport.calls) == 3

    def test_default_retries_is_five(self):
        """默认重试 5 次(总尝试 6):主人拍板 2 次太少,这里钉住防止回退。"""
        client, transport = _client([(503, b"")] * 7)
        with pytest.raises(LLMResponseError) as ei:
            client.chat([{"role": "user", "content": "x"}])
        assert ei.value.status_code == 503
        assert len(transport.calls) == 6  # 1 次 + 默认重试 5 次

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


class TestShortErrorBody:
    """_short:网关/反代的 HTML 错误页只留 <title>,别把整页标记塞进 detail。"""

    def test_html_page_extracts_title(self):
        raw = (
            b"<html> <head> <title>500 Internal Privoxy Error</title>"
            b" <link rel='shortcut icon' href='http://config.privoxy.org/error-favicon.ico'>"
            b" </head> <body> <h1>500 Internal Privoxy Error</h1> </body> </html>"
        )
        assert _short(raw) == "500 Internal Privoxy Error"

    def test_html_page_without_title_falls_back(self):
        assert _short(b"<html><body>boom</body></html>") == "(HTML 错误页,无标题)"

    def test_plain_text_still_truncated(self):
        assert _short(b"plain upstream error", limit=5) == "plain"

    def test_angle_brackets_in_plain_body_not_treated_as_html(self):
        # 正文里带尖括号(如 <reference_files> 块说明)不算 HTML 页,原样保留
        assert _short("用 <reference_files> 块传附件".encode()) == "用 <reference_files> 块传附件"


class _StreamTransport:
    """流式测试 transport:request_stream 返回预置 SSE 行;request 不应被走到。"""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):  # pragma: no cover
        raise AssertionError("本测试不应走非流式 request")

    def request_stream(self, method, url, headers, body, timeout):
        self.calls.append({"body": body, "timeout": timeout})
        return 200, {}, iter((line + "\n").encode() for line in self.lines)


def _stream_client(lines):
    transport = _StreamTransport(lines)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-stream-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def _sse_line(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def _tc_delta(index: int, **kw: str) -> dict:
    """构造一条流式 tool_call 分片:id/name 只在首帧,arguments 逐帧追加。"""
    tc: dict = {"index": index}
    if "id" in kw:
        tc["id"] = kw["id"]
        tc["type"] = "function"
    fn = {k: v for k, v in kw.items() if k in ("name", "arguments")}
    if fn:
        tc["function"] = fn
    return tc


class TestStreamFunctionCalling:
    """chat_stream 的 tools 透传 + tool_calls 流式分片拼装(ADR 0010 流式批)。"""

    def test_stream_tools_passthrough_and_tool_calls_assembled(self):
        raw_query = json.dumps({"query": "闭包"}, ensure_ascii=False)
        first, mid, last = raw_query[:3], raw_query[3:8], raw_query[8:]
        lines = [
            _sse_line(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    _tc_delta(0, id="call_0", name="search_kb", arguments="")
                                ]
                            }
                        }
                    ]
                }
            ),
            _sse_line({"choices": [{"delta": {"tool_calls": [_tc_delta(0, arguments=first)]}}]}),
            _sse_line(
                {"choices": [{"delta": {"tool_calls": [_tc_delta(0, arguments=mid + last)]}}]}
            ),
            "data: [DONE]",
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_kb",
                    "description": "再搜一遍",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ]
        client, transport = _stream_client(lines)
        events = list(
            client.chat_stream(
                [{"role": "user", "content": "x"}], tools=tools, tool_choice="auto", timeout=12.5
            )
        )
        sent = json.loads(transport.calls[0]["body"])
        assert sent["tools"] == tools  # 透传
        assert sent["tool_choice"] == "auto"
        assert transport.calls[0]["timeout"] == 12.5
        # 分片拼装成与非流式 ChatResult.tool_calls 同形,作为流末事件
        assert events[-1] == {
            "type": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "search_kb", "arguments": raw_query},
                }
            ],
        }

    def test_stream_no_tools_keeps_legacy_shape(self):
        """不传 tools → 流式请求体没有 tools/tool_choice 字段(老行为)。"""
        lines = [
            _sse_line({"choices": [{"delta": {"content": "答"}}]}),
            "data: [DONE]",
        ]
        client, transport = _stream_client(lines)
        events = list(client.chat_stream([{"role": "user", "content": "x"}]))
        sent = json.loads(transport.calls[0]["body"])
        assert "tools" not in sent and "tool_choice" not in sent
        assert events == [{"type": "content", "text": "答"}]  # 没有工具 → 无 tool_calls 事件

    def test_stream_content_then_tool_calls_both_surfaced(self):
        """同一条流先出正文再出工具调用(模型边说边调):两种事件都发。"""
        lines = [
            _sse_line({"choices": [{"delta": {"content": "我先看看。"}}]}),
            _sse_line(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    _tc_delta(
                                        0,
                                        id="call_1",
                                        name="search_web",
                                        arguments='{"query": "x"}',
                                    )
                                ]
                            }
                        }
                    ]
                }
            ),
            "data: [DONE]",
        ]
        client, _transport = _stream_client(lines)
        events = list(client.chat_stream([{"role": "user", "content": "x"}], tools=[]))
        assert [e["type"] for e in events] == ["content", "tool_calls"]


class _BrokenStreamTransport:
    """开流成功但迭代中途抛异常(或开流即抛):模拟网关断流/读超时。"""

    def __init__(self, mid_exc: Exception | None = None, open_exc: Exception | None = None) -> None:
        self.mid_exc = mid_exc
        self.open_exc = open_exc

    def request(self, method, url, headers, body, timeout):  # pragma: no cover
        raise AssertionError("流式测试不应走非流式 request")

    def request_stream(self, method, url, headers, body, timeout):
        if self.open_exc is not None:
            raise self.open_exc

        def _gen():
            yield (_sse_line({"choices": [{"delta": {"content": "前半"}}]}) + "\n").encode()
            assert self.mid_exc is not None
            raise self.mid_exc

        return 200, {}, _gen()


class TestStreamNetworkErrors:
    """流式网络层异常一律包装成 LLMError 子类带原因上抛(2026-11-28 网关诊断定案):
    此前裸抛 OSError → agent 的 except LLMError 接不住 → SSE 端点只能报通用兜底文案,
    真实原因(超时/断流/重置)全被吞。"""

    def _consume_mid(self, mid_exc: Exception) -> LLMError:
        client = LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-stream-SECRET",
            model="fake-model",
            transport=_BrokenStreamTransport(mid_exc=mid_exc),
            sleep=lambda _s: None,
        )
        gen = client.chat_stream([{"role": "user", "content": "x"}])
        assert next(gen) == {"type": "content", "text": "前半"}  # 第一帧正常放行
        with pytest.raises(LLMError) as exc_info:
            next(gen)  # 第二帧迭代时网络层炸
        return exc_info.value

    def test_mid_stream_timeout_wrapped_as_llm_timeout(self):
        exc = self._consume_mid(TimeoutError())
        assert isinstance(exc, LLMTimeoutError)
        assert "流式读取超时" in str(exc)

    def test_mid_stream_connection_reset_wrapped_as_llm_network(self):
        exc = self._consume_mid(ConnectionResetError())
        assert isinstance(exc, LLMNetworkError)
        assert "流式连接中断" in str(exc)

    def test_mid_stream_incomplete_read_wrapped_as_llm_network(self):
        exc = self._consume_mid(http.client.IncompleteRead(b"partial"))
        assert isinstance(exc, LLMNetworkError)
        assert "IncompleteRead" in str(exc)

    def test_open_phase_connection_refused_wrapped_as_llm_network(self):
        client = LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-stream-SECRET",
            model="fake-model",
            transport=_BrokenStreamTransport(open_exc=ConnectionRefusedError()),
            sleep=lambda _s: None,
        )
        with pytest.raises(LLMNetworkError, match="流式连接失败"):
            list(client.chat_stream([{"role": "user", "content": "x"}]))

    def test_open_phase_timeout_wrapped_as_llm_timeout(self):
        client = LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-stream-SECRET",
            model="fake-model",
            transport=_BrokenStreamTransport(open_exc=TimeoutError()),
            sleep=lambda _s: None,
        )
        with pytest.raises(LLMTimeoutError, match="流式连接超时"):
            list(client.chat_stream([{"role": "user", "content": "x"}]))
