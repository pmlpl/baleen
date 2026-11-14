"""organizer.py 单测：mock LLMClient，不联网。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.service.llm.client import LLMError
from app.service.parse.types import ParseResult, ParseStatus
from app.service.rewrite.organizer import (
    OrganizeResult,
    organize_note,
    renumber_headings_in_blocks,
)


# ---- fake LLM client ----

class FakeClient:
    """假 LLMClient：按预设返回 content，记录调用次数。"""

    def __init__(self, responses: list[str], *, model: str = "fake-model", fail_with: type[Exception] | None = None):
        self._responses = list(responses)
        self.model = model
        self.fail_with = fail_with
        self.call_count = 0
        self.received_messages: list[list[dict]] = []

    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 800, response_format: Any = None) -> Any:
        self.received_messages.append(messages)
        self.call_count += 1
        if self.fail_with is not None:
            raise self.fail_with("fake network error")
        content = self._responses.pop(0) if self._responses else ""
        return SimpleNamespace(content=content, total_tokens=100, finish_reason="stop")


def _make_parse_result(text: str = "这是一段关于闭包的技术内容。") -> ParseResult:
    blocks = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
    return ParseResult(parse_status=ParseStatus.OK, blocks=blocks, meta={"chars": len(text)})


_SUCCESS_JSON = (
    '{"organizable": true, "markdown": "# 闭包详解\\n\\n## 闭包的定义\\n\\n闭包是函数和词法环境的组合。", '
    '"quality": "core", "note_type": "concept", "reasons": ["提取了技术内容"]}'
)

_NOT_ORGANIZABLE_JSON = (
    '{"organizable": false, "markdown": "", "quality": "junk", "note_type": "ad", '
    '"reasons": ["全文是课程推广，无技术内容"]}'
)


# ---- renumber_headings_in_blocks ----

def _heading(level: int, text: str) -> dict:
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_renumber_simple() -> None:
    doc = {"type": "doc", "content": [
        _heading(1, "什么是闭包"),
        _para("闭包是..."),
        _heading(2, "形成条件"),
        _para("需要嵌套..."),
        _heading(2, "应用场景"),
        _para("用于回调..."),
    ]}
    result = renumber_headings_in_blocks(doc)
    headings = [b for b in result["content"] if b["type"] == "heading"]
    assert headings[0]["content"][0]["text"] == "一、什么是闭包"
    assert headings[1]["content"][0]["text"] == "1.1 形成条件"
    assert headings[2]["content"][0]["text"] == "1.2 应用场景"


def test_renumber_strips_existing_numbers() -> None:
    """LLM 可能加编号，strip 后重新编号。"""
    doc = {"type": "doc", "content": [
        _heading(1, "一、闭包基础"),
        _para("内容"),
        _heading(2, "2.1 定义"),
        _para("内容"),
    ]}
    result = renumber_headings_in_blocks(doc)
    headings = [b for b in result["content"] if b["type"] == "heading"]
    assert headings[0]["content"][0]["text"] == "一、闭包基础"
    assert headings[1]["content"][0]["text"] == "1.1 定义"


def test_renumber_multi_level() -> None:
    doc = {"type": "doc", "content": [
        _heading(1, "闭包基础"),
        _heading(2, "定义与原理"),
        _heading(3, "词法作用域"),
        _heading(3, "变量捕获"),
        _heading(2, "应用场景"),
        _heading(1, "闭包进阶"),
    ]}
    result = renumber_headings_in_blocks(doc)
    headings = [b["content"][0]["text"] for b in result["content"] if b["type"] == "heading"]
    assert headings == [
        "一、闭包基础", "1.1 定义与原理", "1.1.1 词法作用域",
        "1.1.2 变量捕获", "1.2 应用场景", "二、闭包进阶",
    ]


def test_renumber_no_headings() -> None:
    doc = {"type": "doc", "content": [_para("纯文本无标题")]}
    result = renumber_headings_in_blocks(doc)
    assert result == doc


# ---- organize_note ----

def test_organize_success() -> None:
    client = FakeClient([_SUCCESS_JSON])
    pr = _make_parse_result("闭包是函数和词法环境的组合。闭包可以用于回调。")
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True
    assert result.blocks is not None
    assert result.quality == "core"
    assert result.note_type == "concept"
    assert "提取了技术内容" in result.reasons
    assert result.cost_tokens == 100
    assert result.model == "fake-model"
    assert client.call_count == 1
    # blocks 有标题
    headings = [b for b in result.blocks["content"] if b["type"] == "heading"]
    assert len(headings) >= 1
    # 标题带编号
    assert headings[0]["content"][0]["text"].startswith("一、")


def test_organize_not_organizable() -> None:
    client = FakeClient([_NOT_ORGANIZABLE_JSON])
    pr = _make_parse_result("关注公众号获取更多资源！一键三连！")
    result = organize_note(pr, filename="ad.md", ext=".md", client=client)

    assert result.organizable is False
    assert result.blocks is None
    assert result.quality == "junk"
    assert "课程推广" in result.reasons[0]


def test_organize_json_retry_then_success() -> None:
    bad_json = "这不是JSON"
    client = FakeClient([bad_json, _SUCCESS_JSON])
    pr = _make_parse_result()
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True
    assert result.blocks is not None
    assert client.call_count == 2  # 第一次坏，第二次好


def test_organize_degraded_both_json_bad() -> None:
    client = FakeClient(["坏JSON1", "坏JSON2"])
    pr = _make_parse_result("有内容但LLM输出不可解析。")
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True  # 降级：原文入库
    assert result.blocks is not None
    assert result.blocks == pr.blocks  # 原文 blocks
    assert result.meta.get("degraded") is True
    assert client.call_count == 2


def test_organize_llm_error_raises() -> None:
    """LLMError 向上抛（pipeline 层降级）。"""
    client = FakeClient([], fail_with=LLMError)
    pr = _make_parse_result()
    with pytest.raises(LLMError):
        organize_note(pr, filename="test.md", ext=".md", client=client)


def test_organize_organizable_but_empty_markdown_degrades() -> None:
    json_empty_md = '{"organizable": true, "markdown": "", "quality": "normal", "note_type": "other", "reasons": []}'
    client = FakeClient([json_empty_md])
    pr = _make_parse_result()
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True  # 降级入库
    assert result.blocks == pr.blocks  # 原文
    assert result.meta.get("degraded") is True


def test_organize_prompt_contains_contract() -> None:
    """system prompt 包含 note-format.md 关键规则。"""
    client = FakeClient([_SUCCESS_JSON])
    pr = _make_parse_result()
    organize_note(pr, filename="test.md", ext=".md", client=client)

    system_msg = client.received_messages[0][0]["content"]
    assert "标题" in system_msg
    assert "编号" in system_msg
    assert "代码块" in system_msg
    assert "表格" in system_msg
    assert "内容骨架" in system_msg or "一节讲透" in system_msg
    # quality 评级标准：评整理后的笔记本身，视频/字幕来源不构成降级理由
    assert "评的是你整理出的这篇笔记本身" in system_msg
    assert "不构成降级理由" in system_msg
    assert "知识点完整闭环" in system_msg
    assert "<file_content>" in client.received_messages[0][1]["content"]


def test_organize_content_wrapped_in_file_content_tag() -> None:
    """正文包在 <file_content> 里防注入。"""
    client = FakeClient([_SUCCESS_JSON])
    pr = _make_parse_result("敏感内容")
    organize_note(pr, filename="test.md", ext=".md", client=client)

    user_msg = client.received_messages[0][1]["content"]
    assert "<file_content>" in user_msg
    assert "</file_content>" in user_msg
    assert "敏感内容" in user_msg
