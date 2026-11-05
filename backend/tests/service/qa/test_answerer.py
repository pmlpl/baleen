"""问答 answerer 单测:全注入 fake Transport,**绝不真调 LLM**。

覆盖:prompt 组装([n] 编号 + <context> 包裹 + 问题)、多轮历史(进 messages、
超预算从最旧丢、封顶条数)、命中+历史都空才短路(有历史不短路,追问靠历史)、
缓存字段解析、LLM 认证错误向上抛(不静默)。
"""

from __future__ import annotations

import json

import pytest

from app.service.llm import LLMAuthError, LLMClient, LLMResponseError
from app.service.qa.answerer import (
    ContextHit,
    answer,
    build_qa_messages,
    build_web_answer_messages,
)


class FakeTransport:
    """按顺序返回预置响应;异常项直接抛出。"""

    def __init__(self, responses: list[tuple | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "body": body})
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, raw = item
        return status, {}, raw


def _chat(
    content: str,
    *,
    total: int = 15,
    cache_hit: int | None = None,
    cache_miss: int | None = None,
) -> bytes:
    usage: dict = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": total}
    if cache_hit is not None:
        usage["prompt_cache_hit_tokens"] = cache_hit
    if cache_miss is not None:
        usage["prompt_cache_miss_tokens"] = cache_miss
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": usage,
        }
    ).encode()


def _client(responses) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-qa-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def _hit(
    *,
    note_id: int = 1,
    title: str = "闭包笔记",
    hp: str | None = "一、闭包 > 定义",
    parent: str = "闭包是函数 + 它捕获的外层变量。",
    child: str = "闭包是函数 + 它捕获的外层变量。",
) -> ContextHit:
    return ContextHit(
        note_id=note_id,
        note_title=title,
        heading_path=hp,
        parent_text=parent,
        child_text=child,
    )


def _history() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "闭包是什么"},
        {"role": "assistant", "content": "闭包是函数+捕获的外层变量。[1]"},
    ]


class TestPromptAssembly:
    def test_messages_number_hits_and_wrap_context(self) -> None:
        hits = [_hit(note_id=10), _hit(note_id=20, title="Fetch 指南", hp="七、取消请求")]
        msgs, meta = build_qa_messages("闭包是什么", hits)
        assert msgs[0]["role"] == "system"
        user = msgs[-1]["content"]
        assert "<context>" in user and "</context>" in user
        assert "[1]" in user and "[2]" in user
        assert "闭包笔记" in user and "Fetch 指南" in user
        assert "闭包是什么" in user
        assert meta["n_hits"] == 2

    def test_history_inserted_between_system_and_current(self) -> None:
        msgs, meta = build_qa_messages("那它的三个条件", [_hit()], history=_history())
        assert msgs[0]["role"] == "system"
        assert msgs[1] == {"role": "user", "content": "闭包是什么"}
        assert msgs[2] == {"role": "assistant", "content": "闭包是函数+捕获的外层变量。[1]"}
        assert msgs[-1]["role"] == "user" and "那它的三个条件" in msgs[-1]["content"]
        assert meta["history_kept"] == 2

    def test_history_trimmed_from_oldest_when_over_budget(self) -> None:
        long_msg = "x" * 2000
        history = [{"role": "user", "content": f"旧问题{i}:{long_msg}"} for i in range(10)]
        history.append({"role": "user", "content": "最近的问题"})
        msgs, meta = build_qa_messages("继续", [], history=history, history_budget=2000)
        assert 1 <= meta["history_kept"] < len(history)
        # 最近的一条历史必须保留(在当前 user 消息的前一位)
        assert "最近的问题" in msgs[-2]["content"]

    def test_history_capped_at_max_messages(self) -> None:
        history = [{"role": "user", "content": f"问题{i}"} for i in range(20)]
        msgs, meta = build_qa_messages("继续", [], history=history)
        assert meta["history_kept"] == 20  # 远小于安全网上限 → 原样全量重放(缓存对齐的前提)
        assert "问题19" in msgs[-2]["content"]


class TestAttachments:
    """对话附件(<reference_files> 块):用户临时上传给 AI 看的参考文件,不入库。"""

    def test_block_present_with_numbering_and_before_context(self) -> None:
        """有附件 → <reference_files> 块出现,[附n] 编号,块在 <context> 之前。"""
        atts = [
            {"name": "config.py", "text": "DEBUG = True"},
            {"name": "notes.md", "text": "# 要点"},
        ]
        msgs, meta = build_qa_messages("这段配置什么意思", [_hit()], attachments=atts)
        user = msgs[-1]["content"]
        assert "<reference_files>" in user and "</reference_files>" in user
        assert "[附1]" in user and "config.py" in user and "DEBUG = True" in user
        assert "[附2]" in user and "notes.md" in user
        # 块顺序:reference_files 在 context 之前
        assert user.index("<reference_files>") < user.index("<context>")
        assert meta["attachments"] == 2

    def test_empty_attachments_no_block(self) -> None:
        """没附件 → 不出 <reference_files> 块(与空 ref_notes 同口径)。"""
        msgs, meta = build_qa_messages("问题", [_hit()], attachments=[])
        user = msgs[-1]["content"]
        assert "<reference_files>" not in user
        assert meta["attachments"] == 0

    def test_none_attachments_no_block(self) -> None:
        """attachments=None(默认)→ 不出块。"""
        msgs, meta = build_qa_messages("问题", [_hit()])
        assert "<reference_files>" not in msgs[-1]["content"]
        assert meta["attachments"] == 0

    def test_long_attachment_truncated(self) -> None:
        """单文件超 ATTACHMENT_TOKEN_BUDGET → 截断,块内文本比原文短。"""
        long_text = "x" * 50000  # 远超 6000 token 预算(无论字符/token 比例)
        msgs, _meta = build_qa_messages(
            "总结一下", [], attachments=[{"name": "big.log", "text": long_text}]
        )
        user = msgs[-1]["content"]
        assert "<reference_files>" in user
        body = user.split("<reference_files>\n", 1)[1].split("\n</reference_files>", 1)[0]
        # 截断后正文比原文短得多(原文 5 万字符进不去)
        assert len(body) < len(long_text)
        assert "big.log" in body

    def test_answer_with_attachments_no_hits_no_history_not_short_circuit(self) -> None:
        """挂了附件但没命中没历史 → 不短路(AI 要看附件回答)。"""
        client, transport = _client([(200, _chat("这段配置开了 DEBUG。"))])
        result = answer(
            "这段配置什么意思",
            [],
            attachments=[{"name": "config.py", "text": "DEBUG = True"}],
            client=client,
        )
        assert len(transport.calls) == 1  # 调了 LLM,没短路
        assert "DEBUG" in result.answer

    def test_web_path_also_injects_attachments(self) -> None:
        """联网兜底路径也注入附件块(库外答时仍结合用户上传的参考文件)。"""
        results = [{"title": "搜索结果", "url": "https://x", "content": "片段"}]
        msgs = build_web_answer_messages(
            "问题", results, attachments=[{"name": "a.txt", "text": "参考内容"}]
        )
        user = msgs[-1]["content"]
        assert "<reference_files>" in user and "参考内容" in user
        # 块在 web_context 之前
        assert user.index("<reference_files>") < user.index("<web_context>")

    def test_answer_image_rejected_degrades_without_image(self) -> None:
        """模型拒图(LLMResponseError)→ 去图重试,答案前置降级说明,不抛异常。"""
        client, transport = _client([
            LLMResponseError("请求被拒绝(HTTP 400)", status_code=400),
            (200, _chat("按文本回答。")),
        ])
        result = answer(
            "看图",
            [],
            attachments=[{"name": "a.txt", "text": "参考"}],  # 顺带给文本附件,确保去图后仍有上下文
            images=[{"mime": "image/png", "data_url": "data:image/png;base64,xxx"}],
            client=client,
        )
        assert len(transport.calls) == 2  # 带图失败 + 去图重试
        assert "看不了图" in result.answer
        assert "按文本回答" in result.answer

    def test_answer_image_rejected_no_image_reraises(self) -> None:
        """没挂图时 LLMResponseError 直接抛(不走降级,不吞错)。"""
        client, _transport = _client([LLMResponseError("500", status_code=500)])
        with pytest.raises(LLMResponseError):
            answer("问", [_hit()], client=client)


class TestAnswer:
    def test_no_hits_no_history_short_circuits_no_llm_call(self) -> None:
        client, transport = _client([(200, _chat("不该被调用"))])
        result = answer("任意问题", [], client=client)
        assert result.answer == "笔记库里没有找到相关内容。"
        assert result.citations == []
        assert result.cost_tokens == 0
        assert transport.calls == []  # 没调 LLM

    def test_followup_with_history_but_no_hits_still_calls_llm(self) -> None:
        """追问(「再讲讲」)检索不到新片段时,靠历史答,不短路。"""
        client, transport = _client([(200, _chat("闭包的三个条件:嵌套/引用/暴露。"))])
        result = answer("再讲讲", [], history=_history(), client=client)
        assert len(transport.calls) == 1
        assert result.answer != "笔记库里没有找到相关内容。"
        assert result.citations == []  # 本次没检索,没有出处

    def test_answer_with_hits_maps_citations(self) -> None:
        hits = [
            _hit(note_id=10),
            _hit(note_id=20, title="Fetch 指南", child="用 AbortController 取消"),
        ]
        content = "闭包是函数+捕获的外层变量。[1]\n取消请求用 AbortController。[2]"
        client, transport = _client([(200, _chat(content, total=42))])
        result = answer("闭包和取消请求", hits, client=client)
        assert "闭包" in result.answer and "[1]" in result.answer
        assert len(result.citations) == 2
        assert result.citations[0].n == 1 and result.citations[0].note_id == 10
        assert result.citations[1].n == 2 and result.citations[1].note_id == 20
        assert result.citations[0].snippet.startswith("闭包是函数")
        assert result.cost_tokens == 42
        assert result.model == "fake-model"
        assert transport.calls and transport.calls[0]["method"] == "POST"

    def test_citations_filtered_to_referenced_only(self) -> None:
        """答案只引了 [1] → 出处只留第 1 条,不把没被引用的命中硬挂上。"""
        hits = [
            _hit(note_id=10),
            _hit(note_id=20, title="Fetch 指南", child="用 AbortController 取消"),
        ]
        client, _ = _client([(200, _chat("闭包是函数+捕获的外层变量。[1]"))])
        result = answer("闭包", hits, client=client)
        assert len(result.citations) == 1
        assert result.citations[0].n == 1
        assert result.citations[0].note_id == 10

    def test_citations_empty_when_answer_has_no_markers(self) -> None:
        """问候/闲聊没有 [n] → 出处为空(修「发你好也挂 5 条不相干出处」)。"""
        client, _ = _client([(200, _chat("你好!有什么我可以帮你的吗?"))])
        result = answer("你好", [_hit()], client=client)
        assert result.citations == []

    def test_citations_drop_out_of_range_markers(self) -> None:
        """幻觉编号(超过命中数)剔除,不炸。"""
        client, _ = _client([(200, _chat("闭包是……[9]"))])
        result = answer("闭包", [_hit()], client=client)
        assert result.citations == []

    def test_cache_fields_parsed(self) -> None:
        client, _ = _client([(200, _chat("答案 [1]", total=42, cache_hit=30, cache_miss=10))])
        result = answer("问题", [_hit()], client=client)
        assert result.prompt_cache_hit_tokens == 30
        assert result.prompt_cache_miss_tokens == 10

    def test_cache_fields_none_when_provider_omits(self) -> None:
        """OpenAI 系 provider 没有 cache 字段 → None,不炸。"""
        client, _ = _client([(200, _chat("答案 [1]"))])
        result = answer("问题", [_hit()], client=client)
        assert result.prompt_cache_hit_tokens is None
        assert result.prompt_cache_miss_tokens is None

    def test_cache_fields_openai_style_parsed(self) -> None:
        """OpenAI 官方风格:usage.prompt_tokens_details.cached_tokens 也认(网关 SenseNova 渠道是这种,
        未命中时上游整个不带该字段 → None)。"""
        raw = json.dumps(
            {
                "model": "fake-model",
                "choices": [{"message": {"role": "assistant", "content": "答案 [1]"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "total_tokens": 105,
                    "prompt_tokens_details": {"cached_tokens": 80},
                },
            }
        ).encode()
        client, _ = _client([(200, raw)])
        result = answer("问题", [_hit()], client=client)
        assert result.prompt_cache_hit_tokens == 80
        assert result.prompt_cache_miss_tokens == 20  # prompt_tokens - hit

    def test_empty_content_falls_back_to_not_found(self) -> None:
        """reasoning 模型偶发 content 为空时,兜底成「库里没有」,不返回空答案。"""
        client, _ = _client([(200, _chat("", total=8))])
        result = answer("问题", [_hit()], client=client)
        assert result.answer == "笔记库里没有找到相关内容。"

    def test_llm_auth_error_propagates_not_silenced(self) -> None:
        client, _ = _client([(401, b'{"error":{"message":"unauthorized"}}')])
        with pytest.raises(LLMAuthError):
            answer("问题", [_hit()], client=client)
