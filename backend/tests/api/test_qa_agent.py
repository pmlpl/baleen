"""问答 agent 的 API 级测试(ADR 0010):默认开(迁移 0017)=agent loop + trace 落库;
显式关=旧决策树字节一致;超步数回落。

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
    def __init__(self, responses: list[tuple], stream_groups: list[list[str]]) -> None:
        self._responses = responses
        self.calls: list[dict] = []
        # 流式调用(request_stream)按组弹:每组 = 一次 LLM 流式响应的 SSE 行列表
        self.stream_groups = stream_groups

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw

    def request_stream(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body, "stream": True})
        lines = self.stream_groups.pop(0)
        return 200, {}, iter((line + "\n").encode() for line in lines)


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
    events: dict[str, Any] = {"deltas": [], "statuses": []}
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
        elif event == "status":
            events["statuses"].append(parsed["text"])
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
        stream_groups=[],  # 流式调用(request_stream)的 SSE 行组,按组弹
        queries=[],
        transports=[],
    )

    async def fake_retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw: Any):
        state.queries.append(query)
        return SimpleNamespace(query=query, hits=list(state.hits))

    def fake_llm() -> LLMClient:
        transport = FakeTransport(state.responses, state.stream_groups)
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


class TestDefaultOn:
    """默认开(迁移 0017,主人 2026-11-20 验收拍板:不手动开,agent 就是问答模式)。"""

    def test_agent_on_by_default_without_touching_settings(self, env, agent_setup):
        """不动设置直接问 → agent loop:请求带 tools,trace 落在消息上。"""
        agent_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]", total=42)))
        r = env.client.post("/api/qa/chat", json={"question": "闭包是什么"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["assistant_message"]["agent_trace"] is not None  # agent 跑了
        sent = json.loads(agent_setup.transports[-1].calls[0]["body"])
        assert "tools" in sent and sent["tool_choice"] == "auto"
        assert body["assistant_message"]["citations"][0]["note_id"] == 10

    def test_user_without_settings_row_defaults_on(self, env, agent_setup):
        """注册一个全新账号(无 app_settings 行)→ resolve 走「行不存在=开」分支。"""
        reg = env.client.post(
            "/api/auth/register",
            json={"username": "agent-default-on-probe", "password": "probe-pass-123"},
        )
        assert reg.status_code in (200, 201), reg.text
        token = reg.json()["access_token"]
        resp = env.client.get(
            "/api/settings", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["agent_enabled"] is True


class TestSwitchOff:
    def test_explicit_off_walks_legacy_tree(self, env, agent_setup):
        """显式关(PUT false) = 紧急开关/回归口径:走旧决策树,请求体无 tools 字段。"""
        env.client.put("/api/settings", json={"agent_enabled": False})
        resp = env.client.get("/api/settings")
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

    def test_stream_agent_path_streams_answer_with_status_and_trace(self, env, agent_setup):
        """流式端点:agent 最终答案**逐段放行**(多帧 delta,不再整段跳出),工具轮发 status。

        主人验收反馈:不要「思考中」之后直接跳出整段内容。
        """
        env.client.put("/api/settings", json={"agent_enabled": True})
        tool_query = json.dumps({"query": "闭包 定义 例子"}, ensure_ascii=False)
        agent_setup.stream_groups.extend(
            [
                # 第一步(流式):工具调用分片
                [
                    "data: "
                    + json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "call_0",
                                                "type": "function",
                                                "function": {"name": "search_kb", "arguments": ""},
                                            }
                                        ]
                                    }
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {"index": 0, "function": {"arguments": tool_query}}
                                        ]
                                    }
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    "data: [DONE]",
                ],
                # 第二步(流式):最终答案分两段增量 + usage
                [
                    "data: " + json.dumps({"choices": [{"delta": {"content": "闭包是函数"}}]}, ensure_ascii=False),
                    "data: "
                    + json.dumps({"choices": [{"delta": {"content": "+捕获的外层变量。[2]\n"}}]}, ensure_ascii=False),
                    "data: "
                    + json.dumps(
                        {
                            "choices": [],
                            "usage": {"prompt_tokens": 90, "total_tokens": 130},
                        },
                        ensure_ascii=False,
                    ),
                    "data: [DONE]",
                ],
            ]
        )
        with env.client.stream(
            "POST", "/api/qa/chat/stream", json={"question": "闭包是什么"}
        ) as resp:
            text = "".join(resp.iter_text())
        events = _parse_sse(text)
        assert events["meta"]["agent"] is True
        # 工具轮进度可见(status 事件,带换的词)
        assert len(events["statuses"]) == 1
        assert "闭包 定义 例子" in events["statuses"][0]
        # 逐段放行:delta 帧拼起来 = 完整答案
        assert events["delta_text"] == "闭包是函数+捕获的外层变量。[2]\n"
        assert len(events["deltas"]) >= 1  # 逐段放行(首行门可能合帧,拼起来对即可)
        # done:trace 每步 + 引用映射回真实 note_id
        done = events["done"]["assistant_message"]
        assert done["agent_trace"] is not None
        assert [s["kind"] for s in done["agent_trace"]] == ["llm", "tool", "llm"]
        assert done["citations"][0]["n"] == 2
        assert done["citations"][0]["note_id"] == 10
        assert done["cost_tokens"] == 130  # 流式 usage 块进计数
