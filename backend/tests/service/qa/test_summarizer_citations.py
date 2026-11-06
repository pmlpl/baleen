"""summarizer 的引用处理:重编号 + 文末出处清单(纯函数,无需 DB/LLM)。

背景:每条回答的 [1]/[W1] 都是**本地编号**(从 1 开始),把整段会话拼成一篇笔记
必然串号。这里锁住三件事:
① 各条消息的引用合并成全文唯一编号,顺序按首次出现;
② 不同消息里指向同一个地址的引用合并成同一条;
③ 附录只列正文真的用到的编号,联网写 autolink、库内写站内路由。
"""

from __future__ import annotations

from app.service.qa.summarizer import (
    append_citation_appendix,
    note_dialogue,
    renumber_citations,
)


def _web(n: int, title: str, url: str) -> dict:
    return {"n": n, "note_id": None, "note_title": title, "heading_path": None, "url": url}


def _kb(n: int, note_id: int, title: str, heading: str | None = None) -> dict:
    return {
        "n": n,
        "note_id": note_id,
        "note_title": title,
        "heading_path": heading,
        "url": None,
    }


def test_renumber_is_global_across_messages() -> None:
    """两条回答各自的 [W1] 说的不是同一篇文章,合并后必须变成不同编号。"""
    messages = [
        {"role": "user", "content": "触发器", "citations": []},
        {
            "role": "assistant",
            "content": "触发器定义 [W1]。",
            "citations": [_web(1, "respond.io", "https://a.example/x")],
        },
        {"role": "user", "content": "设计类型", "citations": []},
        {
            "role": "assistant",
            "content": "五种模式 [W1][W2]。",
            "citations": [
                _web(1, "优设", "https://b.example/y"),
                _web(2, "博客园", "https://c.example/z"),
            ],
        },
    ]
    renamed, citations = renumber_citations(messages)

    assert renamed[1]["content"] == "触发器定义 [W1]。"
    assert renamed[3]["content"] == "五种模式 [W2][W3]。"
    assert [(c["label"], c["note_title"]) for c in citations] == [
        ("W1", "respond.io"),
        ("W2", "优设"),
        ("W3", "博客园"),
    ]


def test_chained_citations_both_renumbered() -> None:
    """连锁写法 [W1][W2]:第二个前面紧挨着 ']',不能被前置断言整条跳过。"""
    messages = [
        {
            "role": "assistant",
            "content": "要点见 [W1][W2]。",
            "citations": [
                _web(1, "甲", "https://a.example/1"),
                _web(2, "乙", "https://b.example/2"),
            ],
        }
    ]
    renamed, citations = renumber_citations(messages)
    assert renamed[0]["content"] == "要点见 [W1][W2]。"
    assert len(citations) == 2


def test_kb_citation_keeps_separate_namespace() -> None:
    """库内 [1] 与联网 [W1] 各自编号,互不占用对方的号。"""
    messages = [
        {
            "role": "assistant",
            "content": "库内 [1] 和联网 [W1]。",
            "citations": [_kb(1, 161, "Agent 指南"), _web(1, "优设", "https://u.example/x")],
        }
    ]
    renamed, citations = renumber_citations(messages)
    assert renamed[0]["content"] == "库内 [1] 和联网 [W1]。"
    assert {c["label"] for c in citations} == {"1", "W1"}


def test_array_index_not_treated_as_citation() -> None:
    """代码里的 arr[1] 不是引用,不能被改号。"""
    messages = [
        {
            "role": "assistant",
            "content": "取 arr[1] 的值,见 [W1]。",
            "citations": [_web(1, "甲", "https://a.example/1")],
        }
    ]
    renamed, _ = renumber_citations(messages)
    assert "arr[1]" in renamed[0]["content"]
    assert "[W1]" in renamed[0]["content"]


def test_same_source_deduped_to_one_label() -> None:
    """两条回答引用了同一个网址 → 合并成一个编号。"""
    messages = [
        {"role": "assistant", "content": "A [W1]。", "citations": [_web(1, "甲", "https://a.example/1")]},
        {"role": "assistant", "content": "B [W1]。", "citations": [_web(1, "甲", "https://a.example/1")]},
    ]
    renamed, citations = renumber_citations(messages)
    assert renamed[1]["content"] == "B [W1]。"
    assert len(citations) == 1


def test_note_dialogue_range_limits_citations() -> None:
    """按主题裁区间时,区间外的引用不能跟进这篇笔记。"""
    messages = [
        {"role": "assistant", "content": "第一篇 [W1]。", "citations": [_web(1, "甲", "https://a.example/1")]},
        {"role": "assistant", "content": "第二篇 [W1]。", "citations": [_web(1, "乙", "https://b.example/2")]},
    ]
    dialogue, citations = note_dialogue(messages, 1, 1)
    assert "第二篇" in dialogue and "第一篇" not in dialogue
    assert [c["note_title"] for c in citations] == ["乙"]


def test_appendix_lists_only_used_labels() -> None:
    """模型删掉某个引用时,附录不该硬塞一条没人引用的来源。"""
    citations = [
        {"label": "W1", "note_id": None, "note_title": "甲", "heading_path": None, "url": "https://a.example/1"},
        {"label": "W2", "note_id": None, "note_title": "乙", "heading_path": None, "url": "https://b.example/2"},
    ]
    out = append_citation_appendix("# t\n\n只见 [W1]。", citations)
    assert "## 出处" in out
    assert "https://a.example/1" in out
    assert "https://b.example/2" not in out


def test_appendix_format_web_and_kb() -> None:
    """联网 → autolink;库内 → 站内路由(裸路径,前端自己识别)。"""
    citations = [
        {"label": "W1", "note_id": None, "note_title": "甲", "heading_path": None, "url": "https://a.example/1"},
        {"label": "1", "note_id": 161, "note_title": "Agent 指南",
         "heading_path": "方法", "url": None},
    ]
    out = append_citation_appendix("# t\n\n见 [W1][1]。", citations)
    assert "- [W1] 甲 — <https://a.example/1>" in out
    assert "- [1] Agent 指南 · 方法 — /notes/161" in out


def test_appendix_skipped_when_nothing_cited() -> None:
    """模型把标记全删了 → 不追加空清单。"""
    citations = [
        {"label": "W1", "note_id": None, "note_title": "甲", "heading_path": None, "url": "https://a.example/1"}
    ]
    out = append_citation_appendix("# t\n\n没有标记。", citations)
    assert "## 出处" not in out


def test_appendix_skipped_without_citations() -> None:
    assert append_citation_appendix("# t\n\n正文", []) == "# t\n\n正文"
