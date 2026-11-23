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
from app.service.qa.agent import AGENT_ADDENDUM, run_agent, run_agent_iter
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
    payload = (
        results
        if results is not None
        else [
            {
                "title": "Hadoop 生态",
                "url": "https://example.com/hadoop",
                "content": "HDFS/YARN/MapReduce…",
            }
        ]
    )

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
        asyncio.run(run_agent("闭包是什么", hits, retrieve=retrieve, client=client, owner_id=1))
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

    def test_search_web_fallback_note_left_in_steps(self):
        """两级链降级留痕:search_web 降级后步骤 result 带原因(响应可见处)。"""
        client, _transport = _client(
            [
                (200, _tool_body("search_web", "阿司匹林 布洛芬 同服")),
                (200, _chat("两药不建议同服。[W1]", total=60)),
            ]
        )
        retrieve, _calls = _retrieve_fn({})

        def degraded_web(query, *, api_key, **kw):
            # 模拟真实 websearch.search_web 的降级契约:Tavily 败→DeepSearch 成功
            meta_out = kw.get("meta_out")
            if meta_out is not None:
                meta_out["fallback"] = "Tavily HTTP 432→DeepSearch 兜底"
            return [
                {"title": "用药指南", "url": "https://example.com/drug", "content": "不建议同服…"}
            ]

        result = _run(
            client, retrieve, kb_hits=[], tavily_key="tvly-x", web_search=degraded_web
        )
        assert result is not None
        tool_step = result.meta["agent"]["steps"][1]
        assert tool_step["tool"] == "search_web"
        assert tool_step["result"].startswith("1 results")
        assert "Tavily HTTP 432→DeepSearch 兜底" in tool_step["result"]

    def test_search_web_fallback_status_event_when_streaming(self):
        """流式降级:工具轮 status 之后补发一条「联网搜索降级」status。"""
        groups = [
            [
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_0",
                                            "type": "function",
                                            "function": {"name": "search_web", "arguments": ""},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                                "arguments": json.dumps(
                                                    {"query": "阿司匹林 布洛芬 同服"}
                                                )
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                "data: [DONE]",
            ],
            [
                _sse({"choices": [{"delta": {"content": "两药不建议同服。[W1]"}}]}),
                "data: [DONE]",
            ],
        ]
        client, _transport = _stream_client(groups)
        retrieve, _calls = _retrieve_fn({})

        def degraded_web(query, *, api_key, **kw):
            meta_out = kw.get("meta_out")
            if meta_out is not None:
                meta_out["fallback"] = "Tavily HTTP 432→DeepSearch 兜底"
            return [
                {"title": "用药指南", "url": "https://example.com/drug", "content": "不建议同服…"}
            ]

        events = _run_iter(
            client, retrieve, kb_hits=[], tavily_key="tvly-x", web_search=degraded_web
        )
        statuses = [ev["text"] for ev in events if ev["type"] == "status"]
        assert "联网搜索降级:Tavily HTTP 432→DeepSearch 兜底" in statuses

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
            client,
            retrieve,
            tavily_key="tvly-x",
            has_user_images=True,
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
        result = _run(client, retrieve, tavily_key="tvly-x", has_user_images=True, web_search=web)
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


# ---- 流式模式(run_agent_iter streaming=True):status/delta 事件 + 首行门 ----


class _StreamTransport:
    """流式 transport:每次 request_stream 弹一组预置 SSE 行;request 不应被走到。"""

    def __init__(self, line_groups: list[list[str]]) -> None:
        self._groups = list(line_groups)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):  # pragma: no cover
        raise AssertionError("流式模式不应走非流式 request")

    def request_stream(self, method, url, headers, body, timeout):
        self.calls.append({"body": body})
        lines = self._groups.pop(0)
        return 200, {}, iter((line + "\n").encode() for line in lines)


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def _stream_client(line_groups):
    transport = _StreamTransport(line_groups)
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-stream-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def _run_iter(client, retrieve, **kwargs):
    return asyncio.run(
        _collect_iter(
            kwargs.pop("question", "Hadoop 生态圈有哪些组件"),
            kwargs.pop("kb_hits", [_hit(10)]),
            retrieve,
            client,
            **kwargs,
        )
    )


async def _collect_iter(question, kb_hits, retrieve, client, **kwargs):
    kwargs.setdefault("streaming", True)
    events = []
    async for ev in run_agent_iter(
        question, kb_hits, retrieve=retrieve, client=client, owner_id=1, **kwargs
    ):
        events.append(ev)
    return events


class TestStreamingIter:
    def test_tool_round_then_streamed_answer(self):
        """工具轮发 status(带换的词);最终答案逐段放行;result 带全部留痕。"""
        groups = [
            [
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_0",
                                            "type": "function",
                                            "function": {
                                                "name": "search_kb",
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                _sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                                "arguments": json.dumps({"query": "Hadoop 组件"})
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                "data: [DONE]",
            ],
            [
                _sse({"choices": [{"delta": {"content": "换词再搜后:"}}]}),
                _sse({"choices": [{"delta": {"content": "Hadoop 生态含 HDFS。[2]\n"}}]}),
                "data: "
                + json.dumps(
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 100,
                            "total_tokens": 140,
                            "prompt_tokens_details": {"cached_tokens": 80},
                        },
                    },
                    ensure_ascii=False,
                ),
                "data: [DONE]",
            ],
        ]
        client, _transport = _stream_client(groups)
        retrieve, calls = _retrieve_fn({"Hadoop 组件": [_hit(20, "Hadoop 生态")]})
        events = _run_iter(client, retrieve, kb_hits=[_hit(10)])
        assert calls == ["Hadoop 组件"]  # 换的词真的用于检索
        kinds = [ev["type"] for ev in events]
        assert kinds[0] == "status" and "Hadoop 组件" in events[0]["text"]
        assert kinds[-1] == "result"
        deltas = [ev["text"] for ev in events if ev["type"] == "delta"]
        # 首行门:多段增量可能合帧放行,拼起来 = 完整答案(逐段放行,非整段跳出)
        assert "".join(deltas) == "换词再搜后:Hadoop 生态含 HDFS。[2]\n"
        qa = events[-1]["qa"]
        assert qa.answer == "换词再搜后:Hadoop 生态含 HDFS。[2]"  # strip 后的最终文本
        assert [c.note_id for c in qa.citations] == [20]
        assert qa.cost_tokens == 140  # 流式 usage 块进了计数
        assert qa.prompt_cache_hit_tokens == 80
        assert [s["kind"] for s in qa.meta["agent"]["steps"]] == ["llm", "tool", "llm"]

    def test_no_kb_marker_suppressed_and_bailed_out_as_one_delta(self):
        """流式 [NO_KB]:标记不外泄,一个增量都不放行;兜底文本整段补发。"""
        groups = [
            [
                _sse({"choices": [{"delta": {"content": "[NO_KB]\n"}}]}),
                _sse({"choices": [{"delta": {"content": "Hadoop 生态含 HDFS、YARN 等。"}}]}),
                "data: [DONE]",
            ]
        ]
        client, _transport = _stream_client(groups)
        retrieve, _calls = _retrieve_fn({})
        events = _run_iter(client, retrieve, kb_hits=[], tavily_key=None)
        deltas = [ev["text"] for ev in events if ev["type"] == "delta"]
        assert len(deltas) == 1  # 整段补发
        assert "[NO_KB]" not in deltas[0]
        assert deltas[0].startswith("（笔记库里没有相关内容，以下用 AI 自身的知识回答）")
        assert "HDFS" in deltas[0]
        assert events[-1]["qa"].citations == []

    def test_streaming_fake_citation_kept_in_text_but_filtered_from_citations(self):
        """流式不修复(已放行文本无法收回):死徽章留正文,citations 只留有效编号。"""
        groups = [
            [
                _sse({"choices": [{"delta": {"content": "生态含 HDFS。[9]\n"}}]}),
                "data: [DONE]",
            ]
        ]
        client, _transport = _stream_client(groups)
        retrieve, _calls = _retrieve_fn({})
        events = _run_iter(client, retrieve)
        qa = events[-1]["qa"]
        assert "[9]" in qa.answer  # 死徽章保留(与决策树流式同语义)
        assert qa.citations == []  # 幻觉编号绝不进 citations

    def test_non_streaming_wrapper_still_repairs(self):
        """对照:非流式 run_agent 对同样的假引用走修复轮(两种模式行为差异被钉住)。"""
        client, _transport = _client(
            [
                (200, _chat("生态含 HDFS。[9]", total=30)),
                (200, _chat("生态含 HDFS。[1]", total=30)),
            ]
        )
        retrieve, _calls = _retrieve_fn({})
        result = _run(client, retrieve)
        assert result is not None
        assert "[1]" in result.answer  # 修复成有效编号
        assert any(s["kind"] == "repair" for s in result.meta["agent"]["steps"])
