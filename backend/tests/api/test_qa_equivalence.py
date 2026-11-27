"""chat 与 chat_stream 等价性测试(C1 重构基线,2026-11-24)。

同一输入分别打 POST /api/qa/chat 与 POST /api/qa/chat/stream,断言两端点的
**最终答案 / 引用 / used_web / 检索调用序列**一致 + 流式事件序契约
(meta → status* → delta* → done)。先于重构写、跑绿——重构后必须原样绿,
这是「抽共享实现、对外行为零变化」的基线闸。

已知的契约差异已于 2026-11-27 挂账②收口:meta 事件不再带 used_web(agent 模式
下 meta 时点真值结构性未知,只能瞎报;前端零消费)。两端点的 used_web 都只出自
终值(done 事件 / ChatResponse),口径天然一致。

LLM 双通道:非流式端点(agent/answer/answer_web)走 request 队列;流式端点的
联网回答也走 request 队列(answer_web 非流式),只有流式步(agent loop /
决策树库内分支)走 request_stream 行组。同一场景先编 chat 侧队列、再编
stream 侧队列(队列按请求消费,互不残留)。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.api import library as library_module
from app.api import qa as qa_module
from app.main import app
from app.service.llm import LLMClient

QUESTION = "闭包是什么"
KB_ANSWER = "闭包是函数+捕获的外层变量。[1]"
WEB_ANSWER = "根据网络搜索:闭包是作用域引用机制。(来源见 [W1])"
WEB_RESULT = {
    "title": "闭包本质详解",
    "url": "https://closure.example",
    "content": "闭包的本质是…",
}


class _Transport:
    """非流式走 responses 队列;流式按组弹 stream_groups(每次流式调用一组)。"""

    def __init__(self, responses: list[tuple], stream_groups: list[list[str]]) -> None:
        self._responses = responses
        self._stream_groups = stream_groups
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw

    def request_stream(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body, "stream": True})
        lines = self._stream_groups.pop(0)
        return 200, {}, iter((line + "\n").encode() for line in lines)


def _chat(content: str) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 42},
        }
    ).encode()


def _sse_lines(text_chunks: list[str]) -> list[str]:
    """上游流式 SSE 行:正文增量若干 + usage 块 + [DONE](与 test_qa.py 同构)。"""
    lines = []
    for t in text_chunks:
        lines.append(
            "data: " + json.dumps({"choices": [{"delta": {"content": t}}]}, ensure_ascii=False)
        )
    lines.append(
        "data: "
        + json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "prompt_tokens_details": {"cached_tokens": 60},
                },
            },
            ensure_ascii=False,
        )
    )
    lines.append("data: [DONE]")
    return lines


def _parse_sse_ordered(text: str) -> list[tuple[str, Any]]:
    """SSE 响应 → [(event, data)] 按到达顺序;断言事件序契约用。"""
    out: list[tuple[str, Any]] = []
    for frame in text.split("\n\n"):
        event, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        if event is not None and data is not None:
            out.append((event, json.loads(data)))
    return out


@pytest.fixture
def equiv_setup(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """env + 假检索(记录检索词)+ 假 LLM(双通道队列)+ 假联网搜索(记录查询)。"""
    state = SimpleNamespace(
        hits=[
            SimpleNamespace(
                note_id=10,
                note_title="闭包笔记",
                heading_path="一、闭包",
                parent_text="闭包是函数+捕获的外层变量。",
                child_text="闭包是函数+捕获的外层变量。",
            )
        ],
        responses=[],  # 非流式 request 队列(chat 全部 / stream 的 answer_web)
        stream_groups=[],  # 流式 request_stream 行组(stream 的 agent/库内分支)
        queries=[],  # fake_retrieve 收到的检索词
        search_calls=[],  # fake_search 收到的查询
    )

    async def fake_retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw: Any):
        state.queries.append(query)
        return SimpleNamespace(query=query, hits=list(state.hits))

    def fake_llm() -> LLMClient:
        return LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-equiv-SECRET",
            model="fake-model",
            transport=_Transport(state.responses, state.stream_groups),
            sleep=lambda _s: None,
        )

    def fake_search(query: str, *, api_key: str, **_kw: Any):
        state.search_calls.append(query)
        return [dict(WEB_RESULT)]

    app.dependency_overrides[library_module.get_retrieve] = lambda: fake_retrieve
    app.dependency_overrides[qa_module.get_llm_client] = fake_llm
    monkeypatch.setattr(qa_module, "search_web", fake_search)
    try:
        yield state
    finally:
        app.dependency_overrides.pop(library_module.get_retrieve, None)
        app.dependency_overrides.pop(qa_module.get_llm_client, None)


def _run_chat(env: SimpleNamespace) -> dict:
    resp = env.client.post("/api/qa/chat", json={"question": QUESTION})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _run_stream(env: SimpleNamespace) -> list[tuple[str, Any]]:
    resp = env.client.post("/api/qa/chat/stream", json={"question": QUESTION})
    assert resp.status_code == 200, resp.text
    return _parse_sse_ordered(resp.text)


def _assert_event_contract(events: list[tuple[str, Any]]) -> None:
    """流式事件序契约:首 meta、末 done;status 全部先于 delta;无 error。"""
    names = [e for e, _d in events]
    assert names[0] == "meta", names
    assert names[-1] == "done", names
    assert "error" not in names, names
    first_delta = next((i for i, n in enumerate(names) if n == "delta"), None)
    if first_delta is not None:
        for i, n in enumerate(names):
            if n == "status":
                assert i < first_delta, f"status({i}) 必须先于首个 delta({first_delta}):{names}"


def _get(events: list[tuple[str, Any]], name: str) -> Any:
    return next(d for e, d in events if e == name)


def _slice_calls(seq: list, start: int) -> list:
    return seq[start:]


class TestDecisionTreeEquivalence:
    """agent 显式关(旧决策树):两端点同一输入 → 同一最终行为。"""

    @pytest.fixture(autouse=True)
    def _agent_off(self, env: SimpleNamespace) -> None:
        env.client.put("/api/settings", json={"agent_enabled": False})

    def test_kb_answer_equivalence(
        self, env: SimpleNamespace, equiv_setup: SimpleNamespace
    ) -> None:
        """库内正常回答:答案/引用/used_web/检索序列一致。"""
        equiv_setup.responses.append((200, _chat(KB_ANSWER)))
        q0 = len(equiv_setup.queries)
        chat = _run_chat(env)
        chat_queries = _slice_calls(equiv_setup.queries, q0)
        chat_searches = list(equiv_setup.search_calls)

        equiv_setup.responses.append((200, _chat(KB_ANSWER)))
        equiv_setup.stream_groups.append(_sse_lines([KB_ANSWER]))
        q1 = len(equiv_setup.queries)
        events = _run_stream(env)
        stream_queries = _slice_calls(equiv_setup.queries, q1)
        _assert_event_contract(events)

        done = _get(events, "done")
        assert chat["assistant_message"]["content"] == done["assistant_message"]["content"]
        assert chat["assistant_message"]["citations"] == done["assistant_message"]["citations"]
        assert chat["used_web"] is False
        assert chat_queries == stream_queries == [QUESTION]
        assert equiv_setup.search_calls == chat_searches == []  # 两端都没联网

    def test_precheck_web_equivalence(
        self, env: SimpleNamespace, equiv_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """库内没命中 + 配了 key → 预检联网:两端都走 answer_web,行为一致。"""
        equiv_setup.hits = []

        async def fake_tavily_key(_session, _owner_id):  # noqa: ANN001
            return "tvly-equiv"

        monkeypatch.setattr(qa_module, "resolve_tavily_key", fake_tavily_key)

        equiv_setup.responses.append((200, _chat(WEB_ANSWER)))
        q0 = len(equiv_setup.queries)
        chat = _run_chat(env)
        chat_queries = _slice_calls(equiv_setup.queries, q0)
        chat_searches = list(equiv_setup.search_calls)

        equiv_setup.responses.append((200, _chat(WEB_ANSWER)))
        q1 = len(equiv_setup.queries)
        events = _run_stream(env)
        stream_queries = _slice_calls(equiv_setup.queries, q1)
        _assert_event_contract(events)

        done = _get(events, "done")
        assert chat["assistant_message"]["content"] == done["assistant_message"]["content"]
        assert chat["assistant_message"]["citations"] == done["assistant_message"]["citations"]
        assert chat["used_web"] is True
        assert chat_queries == stream_queries == [QUESTION]
        assert chat_searches == [QUESTION] and equiv_setup.search_calls[len(chat_searches) :] == [
            QUESTION
        ]

    def test_no_kb_web_second_stage_equivalence(
        self, env: SimpleNamespace, equiv_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[NO_KB] 二段联网:最终答案/引用一致;meta 不带 used_web(见模块头)。"""
        no_kb = "[NO_KB]\n闭包的本质是作用域引用机制。"

        async def fake_tavily_key(_session, _owner_id):  # noqa: ANN001
            return "tvly-equiv"

        monkeypatch.setattr(qa_module, "resolve_tavily_key", fake_tavily_key)

        equiv_setup.responses.append((200, _chat(no_kb)))
        equiv_setup.responses.append((200, _chat(WEB_ANSWER)))
        q0 = len(equiv_setup.queries)
        chat = _run_chat(env)
        chat_queries = _slice_calls(equiv_setup.queries, q0)
        chat_searches = list(equiv_setup.search_calls)

        # 流式:库内分支走 stream(标记答案被首行门扣下),联网二段走 answer_web(request)
        equiv_setup.stream_groups.append(_sse_lines(["[NO_KB]\n", "闭包的本质是作用域引用机制。"]))
        equiv_setup.responses.append((200, _chat(WEB_ANSWER)))
        q1 = len(equiv_setup.queries)
        events = _run_stream(env)
        stream_queries = _slice_calls(equiv_setup.queries, q1)
        _assert_event_contract(events)

        meta = _get(events, "meta")
        done = _get(events, "done")
        assert chat["assistant_message"]["content"] == done["assistant_message"]["content"]
        assert chat["assistant_message"]["citations"] == done["assistant_message"]["citations"]
        assert "[NO_KB]" not in done["assistant_message"]["content"]  # 标记不外泄(两端同)
        assert chat["used_web"] is True
        # 挂账②收口:meta 不再带 used_web(时点真值结构性未知),唯一真源 = done 的 citations
        assert "used_web" not in meta
        assert done["assistant_message"]["citations"][0]["url"] == WEB_RESULT["url"]
        assert chat_queries == stream_queries == [QUESTION]
        assert chat_searches == [QUESTION] and equiv_setup.search_calls[len(chat_searches) :] == [
            QUESTION
        ]

    def test_no_kb_self_knowledge_equivalence(
        self, env: SimpleNamespace, equiv_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[NO_KB] 无 key → 自身知识兜底:两端同前缀、同答案、used_web 均 False。"""
        no_kb = "[NO_KB]\n关系型数据库是按表组织的数据库。"

        async def fake_tavily_none(_session, _owner_id):  # noqa: ANN001
            return None

        monkeypatch.setattr(qa_module, "resolve_tavily_key", fake_tavily_none)

        equiv_setup.responses.append((200, _chat(no_kb)))
        q0 = len(equiv_setup.queries)
        chat = _run_chat(env)
        chat_queries = _slice_calls(equiv_setup.queries, q0)

        equiv_setup.stream_groups.append(
            _sse_lines(["[NO_KB]\n", "关系型数据库是按表组织的数据库。"])
        )
        q1 = len(equiv_setup.queries)
        events = _run_stream(env)
        stream_queries = _slice_calls(equiv_setup.queries, q1)
        _assert_event_contract(events)

        done = _get(events, "done")
        assert chat["assistant_message"]["content"] == done["assistant_message"]["content"]
        assert "AI 自身的知识回答" in done["assistant_message"]["content"]
        assert "[NO_KB]" not in done["assistant_message"]["content"]
        assert (
            chat["assistant_message"]["citations"] == done["assistant_message"]["citations"] == []
        )
        assert chat["used_web"] is False
        assert chat_queries == stream_queries == [QUESTION]
        assert equiv_setup.search_calls == []


class TestAgentEquivalence:
    """agent 默认开:两端点同输入 → agent 直答行为一致。"""

    @pytest.fixture(autouse=True)
    def _agent_on(self, env: SimpleNamespace) -> None:
        env.client.put("/api/settings", json={"agent_enabled": True})

    def test_agent_direct_answer_equivalence(
        self, env: SimpleNamespace, equiv_setup: SimpleNamespace
    ) -> None:
        """agent 无工具轮直答:答案/引用一致,trace 都落库。"""
        equiv_setup.responses.append((200, _chat(KB_ANSWER)))
        chat = _run_chat(env)

        equiv_setup.stream_groups.append(_sse_lines([KB_ANSWER]))
        events = _run_stream(env)
        _assert_event_contract(events)

        done = _get(events, "done")
        assert chat["assistant_message"]["content"] == done["assistant_message"]["content"]
        assert chat["assistant_message"]["citations"] == done["assistant_message"]["citations"]
        assert chat["used_web"] is False
        # agent 步骤留痕两端都有(非流式走 run_agent、流式走 run_agent_iter,同一实现)
        assert chat["assistant_message"]["agent_trace"] is not None
        assert done["assistant_message"]["agent_trace"] is not None
