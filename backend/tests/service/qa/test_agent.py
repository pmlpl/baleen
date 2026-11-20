"""问答 agent loop 单测(ADR 0010):全注入 fake Transport + fake retrieve,**绝不真调 LLM**。

覆盖:直接答(零工具轮)、search_kb 续号与真实 note_id 引用、search_web 的 [W n] 与
used_web、挂图不搜网(代码不给工具)、预算三道闸(步数/总预算/LLM 失败)→ 回落 None、
[NO_KB] 兜底、假引用拒收重写一轮/仍假摘掉、trace 每步留痕、首轮消息与现状字节一致(前缀缓存)。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from app.service.llm import LLMClient, LLMResponseError
from app.service.qa.agent import AGENT_ADDENDUM, run_agent
from app.service.qa.answerer import ContextHit, build_qa_messages


class FakeTransport:
    """按顺序返回预置响应;异常项直接抛出。"""

    def __init__(self, responses: list[tuple | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "body": body, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, raw = item
        return status, {}, raw


def _chat(content: str, *, total: int = 15) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": total},
        }
    ).encode()


def _tool_body(name: str, query: str, call_id: str = "call_0") -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps({"query": query}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"total_tokens": 20},
        }
    ).encode()


def _client(responses) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-agent-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def _hit(note_id: int, title: str = "闭包笔记", parent: str | None = None) -> ContextHit:
    text = parent or f"笔记{note_id}的内容:概念、定义与例子。"
    return ContextHit(
        note_id=note_id,
        note_title=title,
        heading_path="一、概念",
        parent_text=text,
        child_text=text[:40],
    )


def _retrieve_fn(hits_by_query: dict[str, list[ContextHit]]):
    """按查询返回预置命中;记录每次检索词(断言「换词再搜」真的发生)。"""
    calls: list[str] = []

    async def retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw: Any):
        calls.append(query)
        return SimpleNamespace(hits=list(hits_by_query.get(query, [])))

    return retrieve, calls


def _web_fn(results: list[dict[str, str]] | None = None):
    """fake Tavily:记录查询;默认返回一条预置结果。"""
    calls: list[str] = []
    payload = results if results is not None else [
        {
            "title": "Hadoop 生态",
            "url": "https://example.com/hadoop",
            "content": "HDFS/YARN/MapReduce…",
        }
    ]

    def web_search(query: str, *, api_key: str, **_kw: Any):
        calls.append(query)
        return list(payload)

    return web_search, calls


def _run(client: LLMClient, retrieve, **kwargs: Any):
    return asyncio.run(
        run_agent(
            "Hadoop 生态圈有哪些组件",
            kwargs.pop("kb_hits", [_hit(10)]),
            retrieve=retrieve,
            client=client,
            owner_id=1,
            **kwargs,
        )
    )


class TestDirectAnswer:
    def test_one_llm_call_answer_with_valid_citation(self):
        client, transport = _client([(200, _chat("Hadoop 生态含 HDFS/YARN 等。[1]", total=42))])
        retrieve, _calls = _retrieve_fn({})
        result = _run(client, retrieve, tavily_key="tvly-x")
        assert result is not None
        assert "HDFS" in result.answer
        assert [c.note_id for c in result.citations] == [10]
        meta = result.meta["agent"]
        assert meta["llm_calls"] == 1
        assert meta["tool_calls"] == {"search_kb": 0, "search_web": 0}
        assert meta["used_web"] is False
        # 请求带了 tools(有 tavily key 时两个工具都注册)
        sent = json.loads(transport.calls[0]["body"])
        assert sent["tool_choice"] == "auto"
        assert [t["function"]["name"] for t in sent["tools"]] == ["search_kb", "search_web"]

    def test_first_request_messages_match_legacy_plus_addendum(self):
        """首轮消息 = 现状字节(system + user 不变,addendum 作为第二条 system)→ 前缀缓存可对齐。"""
        client, _transport = _client([(200, _chat("答 [1]"))])
        retrieve, _calls = _retrieve_fn({})
        hits = [_hit(10), _hit(11, "Fetch 指南")]
        asyncio.run(
            run_agent(
                "闭包是什么", hits, retrieve=retrieve, client=client, owner_id=1
            )
        )
        sent = json.loads(_transport.calls[0]["body"])
        legacy, _meta = build_qa_messages("闭包是什么", hits)
        assert sent["messages"][0] == legacy[0]  # system 一字不差
        assert sent["messages"][1] == {"role": "system", "content": AGENT_ADDENDUM}
        assert sent["messages"][-1] == legacy[-1]  # user 消息一字不差
        assert len(sent["messages"]) == len(legacy) + 1


class TestKbTool:
    def test_search_kb_continues_numbering_and_maps_real_note_id(self):
        """换词再搜 → 结果接着 <context> 续号([1] 之后是 [2]),引用映射真实 note_id。"""
        client, _transport = _client(
            [
                (200, _tool_body("search_kb", "Hadoop 组件 清单")),
                (200, _chat("换词再搜后:Hadoop 生态含 HDFS/YARN/MapReduce。[2]", total=50)),
            ]
        )
        retrieve, calls = _retrieve_fn({"Hadoop 组件 清单": [_hit(20, "Hadoop 生态笔记")]})
        result = _run(client, retrieve, kb_hits=[_hit(10)])
        assert result is not None
        assert calls == ["Hadoop 组件 清单"]  # 换的词真的用于检索
        assert [c.note_id for c in result.citations] == [20]
        meta = result.meta["agent"]
        assert meta["llm_calls"] == 2
        assert meta["tool_calls"]["search_kb"] == 1

    def test_tool_result_message_contains_numbered_entry(self):
        client, transport = _client(
            [
                (200, _tool_body("search_kb", "hadoop 生态")),
                (200, _chat("答 [1]")),
            ]
        )
        retrieve, _calls = _retrieve_fn({"hadoop 生态": [_hit(20, "Hadoop 生态")]})
        _run(client, retrieve, kb_hits=[])
        sent2 = json.loads(transport.calls[1]["body"])
        # 轮次协议:assistant(tool_calls) + tool(结果) 追加在尾部,前缀不动
        assert sent2["messages"][-2]["role"] == "assistant"
        assert sent2["messages"][-2]["tool_calls"][0]["id"] == "call_0"
        assert sent2["messages"][-1]["role"] == "tool"
        assert sent2["messages"][-1]["tool_call_id"] == "call_0"
        assert "[1]" in sent2["messages"][-1]["content"]
        assert "Hadoop 生态" in sent2["messages"][-1]["content"]

    def test_bad_tool_args_told_to_model(self):
        """arguments 不是合法 JSON → 工具结果回错误提示,不炸整轮。"""
        raw = json.dumps(
            {
                "model": "fake-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_0",
                                    "type": "function",
                                    "function": {"name": "search_kb", "arguments": "不是json"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"total_tokens": 20},
            }
        ).encode()
        client, _transport = _client([(200, raw), (200, _chat("答 [1]"))])
        retrieve, calls = _retrieve_fn({})
        result = _run(client, retrieve)
        assert result is not None
        assert calls == []  # 参数坏,没真调检索
        assert result.meta["agent"]["tool_calls"]["search_kb"] == 0


class TestWebTool:
    def test_search_web_citations_and_used_web(self):
        client, _transport = _client(
            [
                (200, _tool_body("search_web", "Hadoop 生态圈组件")),
                (200, _chat("Hadoop 生态含 HDFS/YARN/MapReduce。[W1]", total=60)),
            ]
        )
        retrieve, _calls = _retrieve_fn({})
        web, web_calls = _web_fn()
        result = _run(client, retrieve, kb_hits=[], tavily_key="tvly-x", web_search=web)
        assert result is not None
        assert web_calls == ["Hadoop 生态圈组件"]
        assert result.meta["agent"]["used_web"] is True
        assert result.citations[0].note_id is None
        assert result.citations[0].url == "https://example.com/hadoop"

    def test_no_web_tool_without_tavily_key(self):
        """没配 Tavily → search_web 不注册(请求里只有 search_kb)。"""
        client, transport = _client([(200, _chat("直接答 [1]"))])
        retrieve, _calls = _retrieve_fn({})
        _run(client, retrieve)  # 无 tavily_key
        sent = json.loads(transport.calls[0]["body"])
        assert [t["function"]["name"] for t in sent["tools"]] == ["search_kb"]

    def test_user_images_no_web_tool_even_with_key(self):
        """挂图不搜网(宪法在马具):挂图时给了 key 也不给 search_web 工具。"""
        client, transport = _client([(200, _chat("看图答"))])
        retrieve, _calls = _retrieve_fn({})
        _run(
            client, retrieve, tavily_key="tvly-x", has_user_images=True,
            images=[{"mime": "image/png", "data_url": "data:image/png;base64,x"}],
        )
        sent = json.loads(transport.calls[0]["body"])
        assert [t["function"]["name"] for t in sent["tools"]] == ["search_kb"]

    def test_disallowed_tool_call_answered_in_tool_result(self):
        """挂图时模型仍调用 search_web → 工具结果明说「不允许」,整轮不炸。"""
        client, _transport = _client(
            [
                (200, _tool_body("search_web", "随便搜")),
                (200, _chat("那我看图回答。")),
            ]
        )
        retrieve, _calls = _retrieve_fn({})
        web, web_calls = _web_fn()
        result = _run(
            client, retrieve, tavily_key="tvly-x", has_user_images=True, web_search=web
        )
        assert result is not None
        assert web_calls == []  # 没真联网
        assert result.meta["agent"]["tool_calls"]["search_web"] == 0


class TestBudgetFallback:
    def test_steps_exhausted_returns_none(self):
        """模型每轮都要工具,MAX_STEPS 用完还没出答案 → None(调用方回落决策树)。"""
        responses = [(200, _tool_body("search_kb", f"第{i}次换词")) for i in range(2)]
        client, _transport = _client(responses)
        retrieve, _calls = _retrieve_fn({})
        assert _run(client, retrieve, max_steps=2) is None

    def test_time_budget_returns_none(self):
        client, _transport = _client([(200, _chat("答"))])
        retrieve, _calls = _retrieve_fn({})
        assert _run(client, retrieve, time_budget_s=0.0) is None  # 步前预算闸
        assert _transport.calls == []  # 一次 LLM 都没调

    def test_llm_error_returns_none(self):
        client, _transport = _client([LLMResponseError("服务端错误(HTTP 500)", status_code=500)])
        retrieve, _calls = _retrieve_fn({})
        assert _run(client, retrieve) is None

    def test_empty_answer_returns_none(self):
        client, _transport = _client([(200, _chat("", total=8))])
        retrieve, _calls = _retrieve_fn({})
        assert _run(client, retrieve) is None

    def test_per_step_timeout_passed(self):
        """每步超时用 min(step_timeout, 剩余预算):闸在代码,超了由 client 抛错 → 回落。"""
        client, transport = _client([(200, _chat("答 [1]"))])
        retrieve, _calls = _retrieve_fn({})
        _run(client, retrieve, step_timeout=12.5)
        assert transport.calls[0]["timeout"] == 12.5


class TestNoKb:
    def test_no_kb_flag_self_knowledge_answer(self):
        client, _transport = _client([(200, _chat("[NO_KB]\nHadoop 生态通常包括 HDFS、YARN 等。"))])
        retrieve, _calls = _retrieve_fn({})
        result = _run(client, retrieve, kb_hits=[])
        assert result is not None
        assert result.answer.startswith("（笔记库里没有相关内容，以下用 AI 自身的知识回答）")
        assert "HDFS" in result.answer
        assert result.citations == []
        assert result.meta["agent"]["used_web"] is False

    def test_no_kb_with_user_images_no_prefix(self):
        client, _transport = _client([(200, _chat("[NO_KB]\n图里是 Hadoop 架构图。"))])
        retrieve, _calls = _retrieve_fn({})
        result = _run(client, retrieve, kb_hits=[], has_user_images=True)
        assert result is not None
        assert not result.answer.startswith("（笔记库里没有相关内容")
        assert result.citations == []


class TestCitationValidation:
    def test_fake_citation_rejected_and_repaired(self):
        """假引用([9] 超台账)→ 拒收重写一轮 → 修复后答案的引用有效。"""
        client, _transport = _client(
            [
                (200, _chat("Hadoop 生态含 HDFS 等。[9]", total=30)),
                (200, _chat("Hadoop 生态含 HDFS 等。[1]", total=30)),
            ]
        )
        retrieve, _calls = _retrieve_fn({})
        result = _run(client, retrieve)
        assert result is not None
        assert "[1]" in result.answer
        assert [c.note_id for c in result.citations] == [10]
        meta = result.meta["agent"]
        assert meta["llm_calls"] == 2  # 答案 + 修复轮
        assert any(s["kind"] == "repair" for s in meta["steps"])

    def test_repair_still_bad_strips_markers(self):
        """修复轮仍引用幻觉编号 → 摘掉编号(答案保留),citations 永不造假。"""
        client, _transport = _client(
            [
                (200, _chat("生态含 HDFS。[9]", total=30)),
                (200, _chat("生态含 HDFS,组件很多 [9]。", total=30)),
            ]
        )
        retrieve, _calls = _retrieve_fn({})
        result = _run(client, retrieve)
        assert result is not None
        assert "[9]" not in result.answer
        assert "HDFS" in result.answer
        assert result.citations == []

    def test_no_repair_beyond_step_budget(self):
        """步数用满 → 不再修复轮,直接摘掉幻觉编号(修复轮也计入 MAX_STEPS)。"""
        client, _transport = _client(
            [
                (200, _tool_body("search_kb", "换词")),
                (200, _chat("生态含 HDFS。[9]", total=30)),
            ]
        )
        retrieve, _calls = _retrieve_fn({"换词": [_hit(20, "Hadoop 笔记")]})
        result = _run(client, retrieve, max_steps=2)
        assert result is not None
        assert "[9]" not in result.answer
        assert result.meta["agent"]["llm_calls"] == 2  # 没有第三轮修复


class TestTrace:
    def test_steps_recorded_per_step(self):
        """留痕=授权审计:每步 {step, kind, tool, arg, result, latency_ms}。"""
        client, _transport = _client(
            [
                (200, _tool_body("search_kb", "Hadoop 组件")),
                (200, _chat("答 [1]", total=40)),
            ]
        )
        retrieve, _calls = _retrieve_fn({"Hadoop 组件": [_hit(20, "Hadoop 生态")]})
        result = _run(client, retrieve, kb_hits=[])
        assert result is not None
        steps = result.meta["agent"]["steps"]
        kinds = [s["kind"] for s in steps]
        assert kinds == ["llm", "tool", "llm"]
        tool_step = steps[1]
        assert tool_step["tool"] == "search_kb"
        assert tool_step["arg"] == "Hadoop 组件"
        assert tool_step["result"] == "1 hits"
        assert isinstance(tool_step["latency_ms"], float)
        assert all(s["step"] == i + 1 for i, s in enumerate(steps))
