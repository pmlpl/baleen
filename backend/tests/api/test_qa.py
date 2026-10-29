"""问答 API 测试:TestClient + 假检索 + 假 LLM(FakeTransport),不联网。

qa 端点无状态(不碰 SessionDep),所以不用 env 的事务隔离夹具;
直接 ``TestClient(app)`` + override ``get_retrieve`` / ``get_llm_client``。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

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


def _chat(content: str, *, total: int = 15) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": total},
        }
    ).encode()


def _fake_client(content: str, total: int) -> LLMClient:
    transport = FakeTransport([(200, _chat(content, total=total))])
    return LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-qa-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )


def _hit(note_id: int, title: str, hp: str, child: str) -> SimpleNamespace:
    return SimpleNamespace(
        note_id=note_id,
        note_title=title,
        heading_path=hp,
        parent_text=child * 2,
        child_text=child,
    )


@pytest.fixture
def qa_setup() -> tuple[SimpleNamespace, TestClient]:
    """TestClient + 可控假检索(hits)+ 假 LLM(content)。"""
    state = SimpleNamespace(hits=[], llm_content="闭包是函数+捕获的外层变量。[1]", llm_total=20)

    async def fake_retrieve(query: str, *, top_k: int = 5, owner_id: int = 1, **_kw):
        return SimpleNamespace(query=query, hits=list(state.hits))

    def fake_llm() -> LLMClient:
        return _fake_client(state.llm_content, state.llm_total)

    app.dependency_overrides[library_module.get_retrieve] = lambda: fake_retrieve
    app.dependency_overrides[qa_module.get_llm_client] = fake_llm
    try:
        with TestClient(app) as client:
            yield state, client
    finally:
        app.dependency_overrides.clear()


def test_qa_returns_answer_with_citations(qa_setup) -> None:
    state, client = qa_setup
    state.hits = [_hit(10, "闭包笔记", "一、闭包", "闭包是函数+捕获的外层变量。")]
    resp = client.post("/api/qa", json={"question": "闭包是什么", "top_k": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["question"] == "闭包是什么"
    assert "闭包" in body["answer"]
    assert len(body["citations"]) == 1
    assert body["citations"][0]["note_id"] == 10
    assert body["citations"][0]["n"] == 1
    assert body["took_ms"] >= 0
    assert body["model"] == "fake-model"


def test_qa_empty_hits_short_circuits(qa_setup) -> None:
    state, client = qa_setup
    state.hits = []
    state.llm_content = "不该被调用"
    resp = client.post("/api/qa", json={"question": "随便问"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "笔记库里没有找到相关内容。"
    assert body["citations"] == []
    assert body["cost_tokens"] == 0


def test_qa_blank_question_rejected(qa_setup) -> None:
    _, client = qa_setup
    resp = client.post("/api/qa", json={"question": "   "})
    assert resp.status_code == 422
