"""问答 API 测试(阶段2:会话/消息)。

TestClient + env 夹具(外层事务 + savepoint 事务隔离,需 PostgreSQL,不可用自动 skip)
+ 假检索 + 假 LLM(FakeTransport),不联网。qa 现在落库(qa_sessions/qa_messages),
建会话→提问→落库→重开→删除全链路都在事务里,结束回滚不污染开发库。
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
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw


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
    """env(TestClient + 事务隔离)+ 可控假检索 / 假 LLM(带调用记录)。"""
    state = SimpleNamespace(
        hits=[_hit(10, "闭包笔记", "一、闭包", "闭包是函数+捕获的外层变量。")],
        llm_content="闭包是函数+捕获的外层变量。[1]",
        llm_total=20,
        llm_cache_hit=30,
        llm_cache_miss=10,
        transports=[],
    )

    async def fake_retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw: Any):
        return SimpleNamespace(query=query, hits=list(state.hits))

    def fake_llm() -> LLMClient:
        transport = FakeTransport(
            [
                (
                    200,
                    _chat(
                        state.llm_content,
                        total=state.llm_total,
                        cache_hit=state.llm_cache_hit,
                        cache_miss=state.llm_cache_miss,
                    ),
                )
            ]
        )
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
    resp = env.client.post("/api/qa/chat", json={"question": "闭包是什么"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    session_id = body["session_id"]
    assert body["session_title"] == "闭包是什么"
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["cache_hit_rate"] is None  # user 消息没有缓存数据
    assert body["assistant_message"]["content"].startswith("闭包是函数")
    assert body["assistant_message"]["cache_hit_rate"] == 0.75  # 30/(30+10)
    assert body["assistant_message"]["citations"][0]["note_id"] == 10

    # 落库:侧栏列表 + 重开详情
    listing = env.client.get("/api/qa/sessions").json()
    assert listing["total"] == before + 1
    assert listing["items"][0]["id"] == session_id  # 最新在前
    detail = env.client.get(f"/api/qa/sessions/{session_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "闭包是什么"


def test_chat_second_turn_sends_history(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    first = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()
    sid = first["session_id"]
    qa_setup.llm_content = "三个条件:嵌套/引用/暴露。[1]"

    second = env.client.post(
        "/api/qa/chat", json={"session_id": sid, "question": "那它的三个条件"}
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["session_id"] == sid
    assert body2["assistant_message"]["content"].startswith("三个条件")

    # LLM 收到的 messages = system + 历史 2 条 + 当前 1 条
    req = json.loads(qa_setup.transports[-1].calls[0]["body"])
    msgs = req["messages"]
    assert msgs[0]["role"] == "system"
    assert len(msgs) == 4
    # 历史 user 消息按 llm_content 原样重放(含 <context>) —— 前缀对齐才能命中缓存
    assert msgs[1]["content"].startswith("<context>")
    assert any("闭包是什么" in m["content"] for m in msgs[1:3])
    assert any("闭包是函数" in m["content"] for m in msgs[1:3])


def test_chat_unknown_session_404(env: SimpleNamespace, qa_setup: SimpleNamespace) -> None:
    resp = env.client.post("/api/qa/chat", json={"session_id": 999999, "question": "x"})
    assert resp.status_code == 404


def test_delete_session(env: SimpleNamespace, qa_setup: SimpleNamespace) -> None:
    before = env.client.get("/api/qa/sessions").json()["total"]
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
    from app.api import qa as qa_module

    monkeypatch.setattr(qa_module, "EXPORT_DIR", tmp_path / "exports")
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
