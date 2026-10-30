"""标题结构二次加工单测:编号算法 / 插入防御 / 是否需要整理 / LLM 计划解析。

不碰数据库、不联网(FakeTransport)。
"""

from __future__ import annotations

import json

from app.service.llm import ChatResult, LLMClient
from app.service.parse.blocks import (
    empty_doc,
    heading,
    paragraph_from_text,
)
from app.service.rewrite.outline import (
    HeadingSpec,
    apply_outline,
    needs_rewrite,
    number_headings,
    plan_outline,
)


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


def _chat(content: str) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
    ).encode()


def _client(content: str) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport([(200, _chat(content))])
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-test",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


# ---------------- number_headings ----------------


def test_number_level1_uses_chinese() -> None:
    specs = [
        HeadingSpec(0, 1, "第一章"),
        HeadingSpec(10, 1, "第二章"),
        HeadingSpec(20, 1, "第三章"),
    ]
    out = number_headings(specs)
    assert [s.title for s in out] == ["一、第一章", "二、第二章", "三、第三章"]


def test_number_level2_uses_dotted() -> None:
    specs = [
        HeadingSpec(0, 1, "错误处理"),
        HeadingSpec(5, 2, "fetch 写法"),
        HeadingSpec(10, 2, "axios 写法"),
    ]
    out = number_headings(specs)
    assert [s.title for s in out] == ["一、错误处理", "1.1 fetch 写法", "1.2 axios 写法"]


def test_number_level3_and_reset() -> None:
    """三级编号;进入下一个二级时三级计数器要重置。"""
    specs = [
        HeadingSpec(0, 1, "章一"),
        HeadingSpec(5, 2, "节一"),
        HeadingSpec(8, 3, "小节一"),
        HeadingSpec(12, 3, "小节二"),
        HeadingSpec(20, 2, "节二"),
        HeadingSpec(25, 3, "新小节"),
    ]
    out = number_headings(specs)
    assert [s.title for s in out] == [
        "一、章一",
        "1.1 节一",
        "1.1.1 小节一",
        "1.1.2 小节二",
        "1.2 节二",
        "1.2.1 新小节",
    ]


def test_number_many_level1_beyond_ten() -> None:
    specs = [HeadingSpec(i, 1, f"第{i}章") for i in range(12)]
    out = number_headings(specs)
    assert out[9].title.startswith("十、")
    assert out[10].title.startswith("11、")  # 超出中文数字范围用阿拉伯数字
    assert out[11].title.startswith("12、")


# ---------------- apply_outline ----------------


def test_apply_inserts_heading_before_target() -> None:
    doc = {
        "type": "doc",
        "content": [
            paragraph_from_text("开头"),
            paragraph_from_text("第二段"),
            paragraph_from_text("第三段"),
        ],
    }
    out = apply_outline(doc, [HeadingSpec(2, 2, "2.1 新标题")])
    types = [b["type"] for b in out["content"]]
    assert types == ["paragraph", "paragraph", "heading", "paragraph"]
    assert out["content"][2]["attrs"]["level"] == 2
    assert out["content"][2]["content"][0]["text"] == "2.1 新标题"


def test_apply_does_not_mutate_input() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    original_len = len(doc["content"])
    apply_outline(doc, [HeadingSpec(0, 1, "标题")])
    assert len(doc["content"]) == original_len  # 入参没被改


def test_apply_drops_out_of_range_index() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    out = apply_outline(doc, [HeadingSpec(99, 1, "越界")])
    assert [b["type"] for b in out["content"]] == ["paragraph"]


def test_apply_drops_empty_title() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    out = apply_outline(doc, [HeadingSpec(0, 1, "   ")])
    assert [b["type"] for b in out["content"]] == ["paragraph"]


def test_apply_multiple_at_same_index_keeps_level_order() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    specs = [HeadingSpec(0, 1, "父"), HeadingSpec(0, 2, "子")]
    out = apply_outline(doc, specs)
    assert [b["attrs"]["level"] for b in out["content"] if b["type"] == "heading"] == [1, 2]


def test_apply_clamps_bad_level() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    out = apply_outline(doc, [HeadingSpec(0, 99, "x")])
    assert out["content"][0]["attrs"]["level"] == 6


# ---------------- needs_rewrite ----------------


def test_needs_rewrite_when_too_short() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("短")]}
    assert needs_rewrite(doc) is False


def test_needs_rewrite_when_no_headings_and_long() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("字" * 600)]}
    assert needs_rewrite(doc) is True


def test_needs_rewrite_when_only_one_heading_and_long() -> None:
    doc = {
        "type": "doc",
        "content": [heading(1, "唯一标题"), paragraph_from_text("字" * 600)],
    }
    assert needs_rewrite(doc) is True


def test_needs_rewrite_when_duplicate_sibling_titles() -> None:
    """同级重名(「用 fetch 的场合」出现两次)→ 需要整理。"""
    doc = {
        "type": "doc",
        "content": [
            heading(1, "一、错误处理"),
            heading(2, "用 fetch 的场合"),
            paragraph_from_text("字" * 300),
            heading(2, "用 fetch 的场合"),
            paragraph_from_text("字" * 300),
        ],
    }
    assert needs_rewrite(doc) is True


def test_needs_rewrite_false_when_well_numbered() -> None:
    """结构已经不错:多标题、无重名、都带编号 → 不必再花 LLM。"""
    doc = {
        "type": "doc",
        "content": [
            heading(1, "一、错误处理"),
            heading(2, "1.1 fetch 写法"),
            paragraph_from_text("字" * 300),
            heading(1, "二、并发请求"),
            heading(2, "2.1 并发控制"),
            paragraph_from_text("字" * 300),
        ],
    }
    assert needs_rewrite(doc) is False


def test_needs_rewrite_true_when_mostly_unnumbered() -> None:
    doc = {
        "type": "doc",
        "content": [
            heading(1, "错误处理"),
            heading(2, "fetch 写法"),
            paragraph_from_text("字" * 300),
            heading(2, "axios 写法"),
            paragraph_from_text("字" * 300),
        ],
    }
    assert needs_rewrite(doc) is True


def test_needs_rewrite_empty_doc() -> None:
    assert needs_rewrite(empty_doc()) is False


# ---------------- plan_outline(假 LLM) ----------------


def test_plan_outline_parses_json_array() -> None:
    doc = {
        "type": "doc",
        "content": [paragraph_from_text("a"), paragraph_from_text("b"), paragraph_from_text("c")],
    }
    client, transport = _client('[{"at": 1, "level": 2, "title": "小节甲"}]')
    specs = plan_outline(doc, title="测试", client=client)
    assert len(specs) == 1
    assert specs[0].before_index == 1
    assert specs[0].level == 2
    assert specs[0].title == "小节甲"
    assert len(transport.calls) == 1


def test_plan_outline_strips_code_fence() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a"), paragraph_from_text("b")]}
    client, _ = _client('```json\n[{"at": 0, "level": 1, "title": "标题"}]\n```')
    specs = plan_outline(doc, title="t", client=client)
    assert len(specs) == 1
    assert specs[0].title == "标题"


def test_plan_outline_ignores_garbage() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    client, _ = _client("抱歉,我无法处理")
    assert plan_outline(doc, title="t", client=client) == []


def test_plan_outline_drops_out_of_range_and_empty() -> None:
    doc = {"type": "doc", "content": [paragraph_from_text("a")]}
    client, _ = _client(
        '[{"at": 99, "level": 1, "title": "越界"},'
        ' {"at": 0, "level": 1, "title": ""},'
        ' {"at": 0, "level": 1, "title": "有效"}]'
    )
    specs = plan_outline(doc, title="t", client=client)
    assert [s.title for s in specs] == ["有效"]


def test_plan_outline_prompt_contains_heading_markers() -> None:
    doc = {
        "type": "doc",
        "content": [heading(1, "现有标题"), paragraph_from_text("正文内容" * 10)],
    }
    client, transport = _client("[]")
    plan_outline(doc, title="文档名", client=client)
    raw = transport.calls[0]["body"]
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    text = " ".join(m["content"] for m in json.loads(raw)["messages"])
    assert "现有标题" in text
    assert "heading(L1)" in text
    assert "文档名" in text


def test_plan_outline_sorts_by_position() -> None:
    doc = {
        "type": "doc",
        "content": [paragraph_from_text(str(i)) for i in range(5)],
    }
    client, _ = _client(
        '[{"at": 3, "level": 2, "title": "后"}, {"at": 1, "level": 2, "title": "前"}]'
    )
    specs = plan_outline(doc, title="t", client=client)
    assert [s.before_index for s in specs] == [1, 3]


def test_needs_rewrite_when_chapter_is_empty() -> None:
    """章标题下面既没子标题也没正文 → 结构不完整,值得整理。

    真实案例:《Fetch/Axios》里「八、错误处理对比」下面直接跟「九、…」,
    整章只有一行导语,读者在目录里看不出该章讲了什么。
    """
    doc = {
        "type": "doc",
        "content": [
            heading(2, "一、总览"),
            paragraph_from_text("字" * 300),
            heading(2, "八、错误处理对比"),
            heading(2, "九、什么时候用 fetch？"),
            heading(3, "9.1 用 fetch 的场合"),
            paragraph_from_text("字" * 300),
        ],
    }
    assert needs_rewrite(doc) is True


def test_needs_rewrite_false_when_all_sections_have_children() -> None:
    doc = {
        "type": "doc",
        "content": [
            heading(1, "一、总览"),
            heading(2, "1.1 子节"),
            paragraph_from_text("字" * 300),
            heading(1, "二、细节"),
            heading(2, "2.1 子节"),
            paragraph_from_text("字" * 300),
        ],
    }
    assert needs_rewrite(doc) is False


def test_plan_outline_splits_long_doc_into_segments() -> None:
    """超长文档分段调用:每段问一次 LLM,索引换算回全局。"""
    from app.service.rewrite import outline as outline_mod

    n = outline_mod.CHUNK_BLOCKS * 2 + 10
    doc = {"type": "doc", "content": [paragraph_from_text(f"段{i}") for i in range(n)]}

    class _WindowAware:
        """每次调用都返回「本段第一块之前插标题」,用于验证索引偏移。"""

        model = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            # 从 user prompt 里解析本段起始块号
            import re as _re

            text = messages[1]["content"]
            m = _re.search(r"第 (\d+)-", text)
            first = int(m.group(1)) - 1 if m else 0
            return ChatResult(
                content=json.dumps([{"at": 0, "level": 1, "title": f"段{first}"}]),
                model="fake",
                total_tokens=5,
            )

    fake = _WindowAware()
    specs = outline_mod.plan_outline(doc, title="长文", client=fake)  # type: ignore[arg-type]
    # 3 段 → 3 次调用,索引分别是 0 / CHUNK_BLOCKS / CHUNK_BLOCKS*2
    assert fake.calls == 3
    assert [s.before_index for s in specs] == [
        0,
        outline_mod.CHUNK_BLOCKS,
        outline_mod.CHUNK_BLOCKS * 2,
    ]
