"""file_analyzer 测试:假 client,验证分块/并行提取/合并/上限/降级,零联网。"""

from __future__ import annotations

import asyncio

import pytest

from app.service.llm.client import ChatResult, LLMError
from app.service.qa.file_analyzer import (
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    MAX_CHUNKS,
    REPORT_MAX_CHARS,
    FileTooLargeError,
    analyze,
    split_chunks,
)


class FakeChatClient:
    """记录调用;可注入返回内容或异常(同款于 test_vision.py 的 FakeVisionClient)。"""

    def __init__(self, content: str = "要点", exc: Exception | None = None) -> None:
        self.model = "fake-analyze"
        self.calls: list[dict] = []
        self._content = content
        self._exc = exc

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._exc is not None:
            raise self._exc
        return ChatResult(content=self._content, model=self.model)


def test_split_chunks_overlap_and_count() -> None:
    text = "甲" * (CHUNK_CHARS * 2 + 100)
    chunks = split_chunks(text)
    assert len(chunks) == 3
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    # 步进 = 块长 - 重叠:相邻块首尾应有 overlap 长度的相同内容
    assert chunks[0][-CHUNK_OVERLAP:] == chunks[1][:CHUNK_OVERLAP]


def test_split_chunks_single_block() -> None:
    assert split_chunks("短文本") == ["短文本"]


def test_analyze_maps_each_chunk_no_reduce() -> None:
    # 2 块,每块要点远小于 REPORT_MAX_CHARS → 不触发 reduce
    text = "甲" * (CHUNK_CHARS + 100)
    client = FakeChatClient(content="- 要点A")
    report = asyncio.run(analyze(text, client))
    assert len(client.calls) == 2, "两块各一次 map,无 reduce"
    assert "要点A" in report
    assert "第 1 部分" in report and "第 2 部分" in report


def test_analyze_reduce_when_map_output_exceeds_limit() -> None:
    # 2 块 map 各返回半限长要点 → 合并超 REPORT_MAX_CHARS → 触发 reduce
    long_points = "点" * (REPORT_MAX_CHARS // 2 + 10)
    text = "甲" * (CHUNK_CHARS + 100)
    client = FakeChatClient(content=long_points)
    report = asyncio.run(analyze(text, client))
    assert len(client.calls) == 3, "2 次 map + 1 次 reduce"
    assert len(report) <= REPORT_MAX_CHARS


def test_analyze_chunk_failure_degrades_to_head() -> None:
    text = "块内原文内容。" * 2000  # ≥1 块
    client = FakeChatClient(exc=LLMError("上游挂了"))
    report = asyncio.run(analyze(text, client))
    assert "块内原文内容" in report, "LLM 全挂时降级保留原文头部"


def test_analyze_too_many_chunks_raises() -> None:
    # 块数 = ceil((len - CHUNK_CHARS) / step) + 1;要 > MAX_CHUNKS 块,len 需足够大
    text = "字" * (MAX_CHUNKS * CHUNK_CHARS + 1)
    with pytest.raises(FileTooLargeError):
        asyncio.run(analyze(text, FakeChatClient()))
