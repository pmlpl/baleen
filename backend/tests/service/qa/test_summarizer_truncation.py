"""organize_session_note 的截断处理:提高上限 + finish_reason=length 时续写。

起因:真实笔记被 max_tokens=3000 从中间截断(停在表格第二行,末尾留一个 `|`),
而截断是静默的 —— 上游只回 finish_reason="length",正文看起来「正常结束」。

异步用例按本仓库惯例用 ``asyncio.run``(项目没装 pytest-asyncio)。
"""

from __future__ import annotations

import asyncio

from app.service.llm import LLMError
from app.service.llm.client import ChatResult
from app.service.qa.summarizer import (
    CONTINUE_ROUNDS,
    NOTE_MAX_TOKENS,
    organize_session_note,
)


class FakeClient:
    """按脚本返回 ChatResult,并记录每次调用的 messages / max_tokens。"""

    model = "fake"

    def __init__(self, results: list[ChatResult | Exception]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _ok(text: str) -> ChatResult:
    return ChatResult(content=text, model="fake", finish_reason="stop")


def _cut(text: str) -> ChatResult:
    return ChatResult(content=text, model="fake", finish_reason="length")


def test_max_tokens_raised_above_old_3000() -> None:
    """旧上限 3000 会把多表长笔记截断;新上限必须明显更高。"""
    assert NOTE_MAX_TOKENS >= 8000


def test_normal_finish_makes_single_call() -> None:
    client = FakeClient([_ok("# 笔记\n\n正文")])
    out = asyncio.run(organize_session_note("主题", "用户:a\n\n助手:b", client=client))
    assert out == "# 笔记\n\n正文"
    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] == NOTE_MAX_TOKENS


def test_truncated_output_continues_instead_of_cutting_off() -> None:
    """finish_reason=length → 续写,拼回完整正文,且续写请求带着已生成的部分。"""
    client = FakeClient([_cut("# 笔记\n\n| 维度 | 甲 |\n|"), _ok(" 控制流 | 设计期 |")])
    out = asyncio.run(organize_session_note("主题", "用户:a", client=client))

    assert "# 笔记" in out and "控制流 | 设计期" in out
    assert "⚠️" not in out  # 续写成功 → 不该有截断警告
    assert len(client.calls) == 2
    # 第二轮必须把已生成内容作为 assistant 前缀回传,否则模型会重写整篇
    cont = client.calls[1]["messages"]
    assert any(m["role"] == "assistant" and m["content"].startswith("# 笔记") for m in cont)


def test_still_truncated_after_rounds_gets_explicit_warning() -> None:
    """续写轮次用完仍截断 → 必须明确标注,不能假装完整。"""
    client = FakeClient([_cut("第一段")] + [_cut(f"第{i}段") for i in range(CONTINUE_ROUNDS)])
    out = asyncio.run(organize_session_note("主题", "用户:a", client=client))
    assert "⚠️" in out
    assert "被截断" in out
    assert len(client.calls) == 1 + CONTINUE_ROUNDS


def test_continue_failure_keeps_partial_and_warns() -> None:
    """续写请求失败 → 保留已拿到的部分,并标注截断(不整轮丢掉)。"""
    client = FakeClient([_cut("# 笔记\n\n前半"), LLMError("上游挂了")])
    out = asyncio.run(organize_session_note("主题", "用户:a", client=client))
    assert "# 笔记" in out
    assert "⚠️" in out


def test_empty_content_still_reports_failure() -> None:
    client = FakeClient([_ok("")])
    out = asyncio.run(organize_session_note("主题", "用户:a", client=client))
    assert "整理失败" in out


def test_empty_truncated_output_does_not_loop_forever() -> None:
    """一个字都没生成就 length 截断:没有续写起点,不该空转续写。"""
    client = FakeClient([_cut("")])
    out = asyncio.run(organize_session_note("主题", "用户:a", client=client))
    assert "整理失败" in out
    assert len(client.calls) == 1
