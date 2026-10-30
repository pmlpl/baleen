"""Charter 生成器单元测试:_charter_messages 答案注入 + generate_charter 端到端。

generate_charter 是 async(内部 asyncio.to_thread),用 asyncio.run 驱动;
LLM 用 FakeTransport 假 client(不联网)。
"""

from __future__ import annotations

import asyncio
import json

from app.service.charter.generator import _charter_messages, generate_charter
from app.service.llm import LLMClient


class FakeTransport:
    def __init__(self, responses: list[tuple]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw

    def request_stream(self, *a, **kw):
        raise NotImplementedError


def _chat(content: str, *, total: int = 20) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": total},
        }
    ).encode()


def _fake_client(responses: list[tuple]) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-test",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def test_charter_messages_contains_all_answers() -> None:
    """_charter_messages 的 user prompt 包含所有问卷答案。"""
    answers = {
        "direction": "软件工程",
        "purpose": "课设答辩",
        "collect": ["概念笔记", "技术文档"],
        "reject": ["单据", "空文件"],
        "unsure": "collect_low",
        "extra": "关注前端",
    }
    messages = _charter_messages(answers)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_text = messages[1]["content"]
    assert "软件工程" in user_text
    assert "课设答辩" in user_text
    assert "概念笔记" in user_text
    assert "技术文档" in user_text
    assert "单据" in user_text
    assert "空文件" in user_text
    assert "关注前端" in user_text


def test_charter_messages_unsure_options() -> None:
    """不同 unsure 选项映射到不同的自然语言描述。"""
    base = {
        "direction": "测试",
        "purpose": "测试",
        "collect": [],
        "reject": [],
        "unsure": "reject",
        "extra": "",
    }
    msgs_reject = _charter_messages({**base, "unsure": "reject"})
    assert "宁缺毋滥" in msgs_reject[1]["content"]

    msgs_discuss = _charter_messages({**base, "unsure": "discuss"})
    assert "提出来" in msgs_discuss[1]["content"]


def test_generate_charter_returns_text() -> None:
    """generate_charter 返回 LLM 的 content(asyncio.run 驱动)。"""
    client, transport = _fake_client([(200, _chat("## 这是谁的库\n我是一名工程师。"))])
    answers = {
        "direction": "软件工程",
        "purpose": "课设",
        "collect": ["概念笔记"],
        "reject": ["单据"],
        "unsure": "collect_low",
        "extra": "",
    }
    result = asyncio.run(generate_charter(answers, client=client))
    assert "工程师" in result
    assert len(transport.calls) == 1


def test_generate_charter_empty_content_fallback() -> None:
    """LLM 返回空 content → 返回兜底提示文本(不抛)。"""
    client, _transport = _fake_client([(200, _chat(""))])
    answers = {
        "direction": "测试",
        "purpose": "测试",
        "collect": [],
        "reject": [],
        "unsure": "collect_low",
        "extra": "",
    }
    result = asyncio.run(generate_charter(answers, client=client))
    assert "生成失败" in result
