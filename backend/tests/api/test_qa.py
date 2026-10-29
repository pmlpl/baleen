"""问答 API 测试(阶段2/2.5/流式:会话/消息/问题改写/SSE)。

TestClient + env 夹具(外层事务 + savepoint 事务隔离,需 PostgreSQL,不可用自动 skip)
+ 假检索 + 假 LLM(FakeTransport,不联网)。

假 LLM 的响应走 ``state.responses`` 队列,按调用顺序消费:
- 首轮(无历史):1 次调用 = 回答,不改写
- 追问(有历史):2 次调用 = 先改写后回答
流式端点用 ``state.stream_lines``(SSE 行列表)走 request_stream。
fake_retrieve 记录每次检索词,供断言「改写后的 query 真的用于检索」。
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
    """非流式走 responses 队列;流式走 stream_lines(逐行 SSE)。"""

    def __init__(self, responses: list[tuple], stream_lines: list[str]) -> None:
        self._responses = responses
        self._stream_lines = stream_lines
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw

    def request_stream(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body, "stream": True})
        return 200, {}, iter([(line + "\n").encode() for line in self._stream_lines])


def _chat(
    content: str,
    *,
    total: int = 15,
    cache_hit: int | None = None,
    cache_miss: int | None = None,
) -> bytes:
    usage: dict[str, Any] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": total}
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


def _sse_lines(text_chunks: list[str], *, cache_hit: int, cache_miss: int) -> list[str]:
    """构造上游流式响应的 SSE 行:正文增量若干 + usage 块 + [DONE]。"""
    lines = []
    for t in text_chunks:
        lines.append(
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"content": t}}]}, ensure_ascii=False
            )
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
                    "prompt_tokens_details": {"cached_tokens": cache_hit},
                },
            },
            ensure_ascii=False,
        )
    )
    lines.append("data: [DONE]")
    return lines


def _parse_sse(text: str) -> dict[str, Any]:
    """把 SSE 响应文本解析成 {event: data};同 event 多帧时 delta 拼接正文。"""
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


def _hit(note_id: int, title: str, hp: str, child: str) -> SimpleNamespace:
    return SimpleNamespace(
        note_id=note_id,
        note_title=title,
        heading_path=hp,
        parent_text=child * 2,
        child_text=child,
    )


@pytest.fixture
def qa_setup(env: SimpleNamespace) -> SimpleNamespace:
    """env(TestClient + 事务隔离)+ 可控假检索(记录检索词)/ 假 LLM(队列+流式)。"""
    state = SimpleNamespace(
        hits=[_hit(10, "闭包笔记", "一、闭包", "闭包是函数+捕获的外层变量。")],
        responses=[],  # (status, body) 非流式队列;测试按「改写→回答」顺序填入
        stream_lines=[],  # 流式端点的 SSE 行
        queries=[],  # fake_retrieve 收到的检索词
        transports=[],
    )

    async def fake_retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw: Any):
        state.queries.append(query)
        return SimpleNamespace(query=query, hits=list(state.hits))

    def fake_llm() -> LLMClient:
        transport = FakeTransport(state.responses, state.stream_lines)
        state.transports.append(transport)
        return LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-qa-SECRET",
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


def test_chat_creates_session_and_persists(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    # 库里可能有主人真实的历史会话(浏览器测试留下的),断言只用「前后差值」,不假设空库
    before = env.client.get("/api/qa/sessions").json()["total"]
    qa_setup.responses.append(
        (200, _chat("闭包是函数+捕获的外层变量。[1]", total=42, cache_hit=30, cache_miss=10))
    )
    resp = env.client.post("/api/qa/chat", json={"question": "闭包是什么"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    session_id = body["session_id"]
    assert body["session_title"] == "闭包是什么"
    assert body["search_query"] == "闭包是什么"  # 首轮不改写
    assert body["user_message"]["cache_hit_rate"] is None  # user 消息没有缓存数据
    assert body["assistant_message"]["cache_hit_rate"] == 0.75  # 30/(30+10)
    assert body["assistant_message"]["citations"][0]["note_id"] == 10
    # 首轮只调 1 次 LLM(回答),没有改写调用
    assert len(qa_setup.transports[-1].calls) == 1

    # 落库:侧栏列表 + 重开详情
    listing = env.client.get("/api/qa/sessions").json()
    assert listing["total"] == before + 1
    assert listing["items"][0]["id"] == session_id  # 最新在前
    detail = env.client.get(f"/api/qa/sessions/{session_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "闭包是什么"


def test_chat_followup_retrieves_rewritten_query_and_replays_history(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    qa_setup.responses.append(
        (200, _chat("Promise 是异步操作的结果对象,手写要点如下。[1]"))
    )
    first = env.client.post("/api/qa/chat", json={"question": "什么是 Promise，怎么手写"}).json()
    sid = first["session_id"]

    # 追问:先改写(「它」→「手写实现的 Promise」)再用改写词检索,最后回答
    qa_setup.responses.append((200, _chat("手写实现的 Promise 有哪几种状态")))
    qa_setup.responses.append(
        (200, _chat("手写实现的 Promise 有三种状态:PENDING/FULFILLED/REJECTED。[1]", total=42))
    )
    second = env.client.post(
        "/api/qa/chat", json={"session_id": sid, "question": "它有哪几种状态"}
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["search_query"] == "手写实现的 Promise 有哪几种状态"
    # 改写后的 query 真的用于检索(而不是原话「它有哪几种状态」)
    assert qa_setup.queries[-1] == "手写实现的 Promise 有哪几种状态"

    # 回答那次 LLM 调用:system + 历史 2 条(llm_content 原样重放) + 当前 1 条
    answer_call = qa_setup.transports[-1].calls[-1]
    msgs = json.loads(answer_call["body"])["messages"]
    assert msgs[0]["role"] == "system"
    assert len(msgs) == 4
    assert msgs[1]["content"].startswith("<context>")  # llm_content 原样重放 → 前缀对齐
    assert "它有哪几种状态" in msgs[-1]["content"]


def test_chat_rewrite_failure_falls_back_to_original(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    qa_setup.responses.append((200, _chat("Promise 是异步操作的结果对象。[1]")))
    first = env.client.post("/api/qa/chat", json={"question": "什么是 Promise"}).json()
    sid = first["session_id"]

    # 改写调用 401(LLMAuthError 立即抛,handler 兜底) → 回答成功
    qa_setup.responses.append((401, b'{"error":{"message":"unauthorized"}}'))
    qa_setup.responses.append((200, _chat("基于历史的回答。[1]", total=30)))
    second = env.client.post(
        "/api/qa/chat", json={"session_id": sid, "question": "它有哪几种状态"}
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["search_query"] == "它有哪几种状态"  # 回退原问题
    assert qa_setup.queries[-1] == "它有哪几种状态"
    assert body2["assistant_message"]["content"].startswith("基于历史")


def test_chat_stream_endpoint_metas_deltas_and_persists(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    qa_setup.stream_lines = _sse_lines(
        ["手写实现的 ", "Promise 有三种状态。[1]"], cache_hit=60, cache_miss=40
    )
    resp = env.client.post("/api/qa/chat/stream", json={"question": "闭包是什么"})
    assert resp.status_code == 200, resp.text
    assert "text/event-stream" in resp.headers["content-type"]

    events = _parse_sse(resp.text)
    assert events["meta"]["search_query"] == "闭包是什么"  # 首轮不改写
    assert events["meta"]["user_message"]["content"] == "闭包是什么"
    assert events["delta_text"] == "手写实现的 Promise 有三种状态。[1]"
    # usage 块走 OpenAI 风格 cached_tokens → 归一后 60/(60+40)
    assert events["done"]["assistant_message"]["cache_hit_rate"] == 0.6
    assert events["done"]["assistant_message"]["content"] == "手写实现的 Promise 有三种状态。[1]"
    assert events["done"]["assistant_message"]["citations"][0]["note_id"] == 10

    # 落库:流结束后助手消息已 commit
    sid = events["meta"]["session_id"]
    detail = env.client.get(f"/api/qa/sessions/{sid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "手写实现的 Promise 有三种状态。[1]"


def test_chat_unknown_session_404(env: SimpleNamespace, qa_setup: SimpleNamespace) -> None:
    resp = env.client.post("/api/qa/chat", json={"session_id": 999999, "question": "x"})
    assert resp.status_code == 404


def test_delete_session(env: SimpleNamespace, qa_setup: SimpleNamespace) -> None:
    before = env.client.get("/api/qa/sessions").json()["total"]
    qa_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
    first = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()
    sid = first["session_id"]
    resp = env.client.delete(f"/api/qa/sessions/{sid}")
    assert resp.status_code == 204
    assert env.client.get(f"/api/qa/sessions/{sid}").status_code == 404
    listing = env.client.get("/api/qa/sessions").json()
    assert listing["total"] == before  # 只删了自己建的那条,主人的真实会话不动


def test_blank_question_rejected(env: SimpleNamespace, qa_setup: SimpleNamespace) -> None:
    resp = env.client.post("/api/qa/chat", json={"question": "   "})
    assert resp.status_code == 422


def test_export_session_writes_file_and_returns_markdown(
    env: SimpleNamespace,
    qa_setup: SimpleNamespace,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qa_module, "EXPORT_DIR", tmp_path / "exports")
    qa_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
    first = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()
    sid = first["session_id"]

    resp = env.client.get(f"/api/qa/sessions/{sid}/export")
    assert resp.status_code == 200, resp.text
    assert "text/markdown" in resp.headers["content-type"]
    assert "闭包是什么" in resp.text
    assert "[1] 用户" in resp.text and "[2] 助手" in resp.text
    assert "缓存命中率" in resp.text  # 无数据的消息显示 —

    # 同一份内容落了一份到 tmp/qa_exports/,agent 可直接读
    files = list((tmp_path / "exports").glob("session-*.md"))
    assert len(files) == 1
    assert "闭包是什么" in files[0].read_text(encoding="utf-8")

    # 不存在的会话 → 404
    assert env.client.get("/api/qa/sessions/999999/export").status_code == 404
