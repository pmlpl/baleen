"""对话 digest 单测:摘要 prompt 组装、对话拍平、digest 段拼接与兜底、run_digest 调 LLM。

全注入 fake Transport,绝不真调 LLM。
"""

from __future__ import annotations

import asyncio
import json

from app.service.llm import LLMClient
from app.service.qa.digest import (
    build_digest_messages,
    compose_digest_block,
    dialogue_from_messages,
    run_digest,
)


class _Msg:
    """轻量 QaMessage 替身(dialogue_from_messages 只读 role/content)。"""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class FakeTransport:
    def __init__(self, responses: list[tuple]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw


def _chat(content: str, *, finish_reason: str | None = "stop") -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    ).encode()


def _client(responses) -> LLMClient:
    transport = FakeTransport(responses)
    return LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-digest-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )


class TestBuildDigestMessages:
    def test_system_is_summarizer_contract(self) -> None:
        msgs = build_digest_messages("用户:什么是闭包\n助手:闭包是函数+捕获变量")
        assert msgs[0]["role"] == "system"
        assert "摘要器" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        assert "什么是闭包" in msgs[1]["content"]


class TestDialogueFromMessages:
    def test_flatten_with_role_prefix(self) -> None:
        msgs = [_Msg("user", "什么是闭包"), _Msg("assistant", "闭包是函数+捕获变量")]
        text = dialogue_from_messages(msgs)
        assert "用户:什么是闭包" in text
        assert "助手:闭包是函数+捕获变量" in text

    def test_truncate_when_over_limit(self) -> None:
        long = "x" * 20000
        text = dialogue_from_messages([_Msg("user", long)])
        assert len(text) <= 16000  # DIGEST_DIALOGUE_MAX_CHARS


class TestComposeDigestBlock:
    def test_empty_returns_empty_string(self) -> None:
        assert compose_digest_block([]) == ""
        assert compose_digest_block([""]) == ""
        assert compose_digest_block([None]) == ""  # type: ignore[list-item]

    def test_single_summary_wrapped(self) -> None:
        block = compose_digest_block(["用户问了闭包"])
        assert block.startswith("<prior_summary>")
        assert block.endswith("</prior_summary>")
        assert "用户问了闭包" in block

    def test_multiple_summaries_joined_in_order(self) -> None:
        block = compose_digest_block(["第一段", "第二段"])
        assert block.index("第一段") < block.index("第二段")

    def test_over_budget_keeps_newest_drops_oldest(self) -> None:
        # 每段 ~6000 token(≈ DIGEST_TOTAL_BUDGET),塞 3 段 → 只留最新的,丢最旧
        big = "摘" * 6000  # CJK 1:1 → ~6000 token/段
        block = compose_digest_block([big, big, big + "最新"])
        assert "最新" in block  # 最新段保底留下
        assert "已省略" in block  # 丢了旧段


class TestRunDigest:
    def test_returns_summary_text(self) -> None:
        client = _client([(200, _chat("用户询问了闭包的概念,结论是闭包=函数+捕获变量"))])
        summary = asyncio.run(run_digest("用户:什么是闭包", client=client))
        assert "闭包" in summary

    def test_length_truncation_adds_note(self) -> None:
        client = _client([(200, _chat("一段摘要", finish_reason="length"))])
        summary = asyncio.run(run_digest("对话", client=client))
        assert "截断" in summary

    def test_empty_content_returns_empty(self) -> None:
        client = _client([(200, _chat(""))])
        summary = asyncio.run(run_digest("对话", client=client))
        assert summary == ""
