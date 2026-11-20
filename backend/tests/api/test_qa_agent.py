"""问答 agent 灰度的 API 级测试(ADR 0010):开关默认关=现状字节一致;
开=agent loop + trace 落库;超预算回落决策树。

TestClient + env 夹具(外层事务 + savepoint 隔离,需 PostgreSQL)+ 假检索 + 假 LLM
(FakeTransport,不联网;响应队列支持 tool_calls 轮次)。
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


class FakeTransport:
    def __init__(self, responses: list[tuple]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
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


def _parse_sse(text: str) -> dict[str, Any]:
    events: dict[str, Any] = {"deltas": []}
    for frame in text.split("\n\n"):
        event, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        if event is None or data is None:
            continue
        parsed = json.loads(data)
        if event == "delta":
            events["deltas"].append(parsed["text"])
        else:
            events[event] = parsed
    events["delta_text"] = "".join(events["deltas"])
    return events


@pytest.fixture
def agent_setup(env: SimpleNamespace) -> SimpleNamespace:
    """env(TestClient + 事务隔离)+ 可控假检索/假 LLM(队列支持 tool_calls 轮次)。"""
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
        responses=[],
        queries=[],
        transports=[],
    )

    async def fake_retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw: Any):
        state.queries.append(query)
        return SimpleNamespace(query=query, hits=list(state.hits))

    def fake_llm() -> LLMClient:
        transport = FakeTransport(state.responses)
        state.transports.append(transport)
        return LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-agent-SECRET",
            model="fake-model",
            transport=transport,
            sleep=lambda _s: None,
        )

    app.dependency_overrides[library_module.get_retrieve] = lambda: fake_retrieve
    app.dependency_overrides[qa_module.get_llm_client] = fake_llm
    try:
        yield state
    finally:
        app.dependency_overrides.pop(library_module.get_retrieve, None)
        app.dependency_overrides.pop(qa_module.get_llm_client, None)


class TestSwitchOff:
    def test_agent_off_by_default_walks_legacy_tree(self, env, agent_setup):
        """开关默认关 = 走现有决策树:LLM 请求体里没有 tools 字段(字节与现状一致)。"""
        resp = env.client.get("/api/settings")
        assert resp.status_code == 200, resp.text
        assert resp.json()["agent_enabled"] is False

        agent_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
        r = env.client.post("/api/qa/chat", json={"question": "闭包是什么"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["assistant_message"]["agent_trace"] is None  # 老路径没有 trace
        sent = json.loads(agent_setup.transports[-1].calls[0]["body"])
        assert "tools" not in sent
        assert "tool_choice" not in sent
        assert body["assistant_message"]["citations"][0]["note_id"] == 10


class TestSwitchOn:
    def test_settings_agent_enabled_roundtrip(self, env, agent_setup):
        """设置页开关往返:PUT true → GET true;PUT false → GET false(热生效)。"""
        r = env.client.put("/api/settings", json={"agent_enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["agent_enabled"] is True
        assert env.client.get("/api/settings").json()["agent_enabled"] is True
        env.client.put("/api/settings", json={"agent_enabled": False})
        assert env.client.get("/api/settings").json()["agent_enabled"] is False

    def test_agent_tool_round_answer_with_trace_persisted(self, env, agent_setup):
        """开关开:换词再搜一轮 → 答案引用台账编号;trace 每步落库且随消息透出。"""
        env.client.put("/api/settings", json={"agent_enabled": True})
        agent_setup.responses.append((200, _tool_body("search_kb", "Hadoop 生态 组件")))
        agent_setup.responses.append((200, _chat("换词再搜后:闭包的定义如下。[2]", total=50)))
        r = env.client.post("/api/qa/chat", json={"question": "闭包是什么"})
        assert r.status_code == 200, r.text
        body = r.json()
        # 初始检索 + 脑换词再搜,两次检索都发生了
        assert agent_setup.queries == ["闭包是什么", "Hadoop 生态 组件"]
        # 引用 [2] = 工具结果的续号,映射回真实 note_id
        assert body["assistant_message"]["citations"][0]["n"] == 2
        assert body["assistant_message"]["citations"][0]["note_id"] == 10
        trace = body["assistant_message"]["agent_trace"]
        assert trace is not None
        assert [s["kind"] for s in trace] == ["llm", "tool", "llm"]
        assert trace[1]["tool"] == "search_kb"
        assert trace[1]["arg"] == "Hadoop 生态 组件"

        # trace 落库(授权审计):直接查 qa_messages.trace
        assistant_id = body["assistant_message"]["id"]

        async def _read_trace():
            from sqlalchemy import select

            from app.models.qa import QaMessage

            async with env.shared["factory"]() as session:
                row = await session.scalar(select(QaMessage).where(QaMessage.id == assistant_id))
                return row.trace

        db_trace = env.client.portal.call(_read_trace)
        assert db_trace is not None
        assert [s["kind"] for s in db_trace] == ["llm", "tool", "llm"]

        # 前端会话详情也透出(GET /sessions/{id})
        detail = env.client.get(f"/api/qa/sessions/{body['session_id']}").json()
        assert detail["messages"][1]["agent_trace"] == trace

    def test_agent_steps_exhausted_falls_back_to_legacy(self, env, agent_setup):
        """超预算回落:脑每轮都要工具,MAX_STEPS(6)耗尽 → 回落现有决策树,照常出答案。"""
        from app.service.qa.agent import AGENT_MAX_STEPS

        env.client.put("/api/settings", json={"agent_enabled": True})
        # 6 轮工具调用耗尽步数预算 + 第 7 次调用留给决策树的 answer()
        agent_setup.responses.extend(
            (200, _tool_body("search_kb", f"第{i}次换词")) for i in range(AGENT_MAX_STEPS)
        )
        agent_setup.responses.append((200, _chat("回落决策树后:闭包是函数+捕获的外层变量。[1]")))
        r = env.client.post("/api/qa/chat", json={"question": "闭包是什么"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "回落决策树后" in body["assistant_message"]["content"]
        assert body["assistant_message"]["agent_trace"] is None  # 回落 = 老路径,无 trace
        # 6 轮 agent + 1 轮决策树
        assert len(agent_setup.transports[-1].calls) == AGENT_MAX_STEPS + 1

    def test_stream_agent_path_emits_single_delta_with_trace(self, env, agent_setup):
        """流式端点:agent 路径答案作为一帧 delta 下发,meta 带 agent 标记,done 带 trace。"""
        env.client.put("/api/settings", json={"agent_enabled": True})
        agent_setup.responses.append((200, _tool_body("search_kb", "闭包 定义 例子")))
        agent_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[2]", total=44)))
        with env.client.stream(
            "POST", "/api/qa/chat/stream", json={"question": "闭包是什么"}
        ) as resp:
            text = "".join(resp.iter_text())
        events = _parse_sse(text)
        assert events["meta"]["agent"] is True
        assert events["delta_text"] == "闭包是函数+捕获的外层变量。[2]"
        assert events["done"]["assistant_message"]["agent_trace"] is not None
        assert [s["kind"] for s in events["done"]["assistant_message"]["agent_trace"]] == [
            "llm",
            "tool",
            "llm",
        ]
