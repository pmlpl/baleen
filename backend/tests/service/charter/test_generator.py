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
    """_charter_messages 的 user prompt 包含全部六个事实(2026-11-26 事实化问卷)。"""
    answers = {
        "nickname": "小明",
        "age_band": "18-24",
        "gender": "男",
        "education": "本科在读",
        "status": "在读学生",
        "why": "课设答辩沉淀",
    }
    messages = _charter_messages(answers)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_text = messages[1]["content"]
    for fact in ("小明", "18-24", "男", "本科在读", "在读学生", "课设答辩沉淀"):
        assert fact in user_text, f"事实「{fact}」必须进 user prompt"


def test_charter_messages_bakes_fixed_rules() -> None:
    """拿不准口径不再问卷化:固定「宁收低质量」+ 事实推导口径写进 system prompt。"""
    messages = _charter_messages({})
    system_text = messages[0]["content"]
    assert "拿不准就收下" in system_text
    assert "推导" in system_text


def test_generate_charter_returns_text() -> None:
    """generate_charter 返回 LLM 的 content(asyncio.run 驱动)。"""
    client, transport = _fake_client([(200, _chat("## 这是谁的库\n「小明」的库,一名工程师。"))])
    answers = {
        "nickname": "小明",
        "age_band": "18-24",
        "gender": "男",
        "education": "本科",
        "status": "在职",
        "why": "工作沉淀",
    }
    result = asyncio.run(generate_charter(answers, client=client))
    assert "工程师" in result
    assert len(transport.calls) == 1


def test_generate_charter_empty_content_fallback() -> None:
    """LLM 返回空 content → 返回兜底提示文本(不抛)。"""
    client, _transport = _fake_client([(200, _chat(""))])
    answers = {
        "nickname": "",
        "age_band": "",
        "gender": "",
        "education": "",
        "status": "",
        "why": "",
    }
    result = asyncio.run(generate_charter(answers, client=client))
    assert "生成失败" in result
