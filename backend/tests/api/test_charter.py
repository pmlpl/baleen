"""Charter API 测试:GET 查状态、PUT 存 charter、POST generate(LLM 生成不落库)。

env 夹具提供 TestClient + 事务隔离(每条测试回滚,不污染 dev 库)。
generate 端点用 FakeTransport 假 LLM(不联网)。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.api import charter as charter_module
from app.main import app
from app.service.llm import LLMClient


class FakeTransport:
    """非流式假 transport:按 responses 队列返回 (status, headers, body)。"""

    def __init__(self, responses: list[tuple]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw

    def request_stream(self, *a, **kw):
        raise NotImplementedError


def _chat(content: str, *, total: int = 20) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": total},
        }
    ).encode()


def _clear(env: SimpleNamespace) -> None:
    """把该账号的 charter 清空(UPDATE 空串 = 清空,回退 charter.md)。"""
    assert env.client.put("/api/charter", json={"charter": ""}).status_code == 200


@pytest.fixture
def charter_llm(env: SimpleNamespace) -> SimpleNamespace:
    """env + 假 LLM(覆盖 charter_module.get_llm_client)。"""
    state = SimpleNamespace(responses=[], transports=[])

    def fake_llm() -> LLMClient:
        transport = FakeTransport(state.responses)
        state.transports.append(transport)
        return LLMClient(
            base_url="https://api.example.test/v1",
            api_key="sk-charter-SECRET",
            model="fake-model",
            transport=transport,
            sleep=lambda _s: None,
        )

    app.dependency_overrides[charter_module.get_llm_client] = fake_llm
    try:
        yield state
    finally:
        app.dependency_overrides.pop(charter_module.get_llm_client, None)


def test_get_no_charter(env: SimpleNamespace) -> None:
    """DB 没有 charter(刚加的列,默认 NULL)→ has_charter=false, charter=null。"""
    # 夹具账号可能已有章程(开发库残留),先清空再断言
    _clear(env)
    resp = env.client.get("/api/charter")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_charter"] is False
    assert body["charter"] is None


def test_put_then_get(env: SimpleNamespace) -> None:
    """PUT 存 charter → GET 有 charter + has_charter=true。"""
    text = "## 这是谁的库\n我是一名测试工程师。\n\n## 收什么\n- 技术笔记"
    resp = env.client.put("/api/charter", json={"charter": text})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_charter"] is True
    assert body["charter"] == text

    # GET 确认持久(同一事务内)
    body = env.client.get("/api/charter").json()
    assert body["has_charter"] is True
    assert body["charter"] == text


def test_generate_returns_charter_text(
    env: SimpleNamespace, charter_llm: SimpleNamespace
) -> None:
    """POST generate → LLM 生成 charter 文本(不落库,GET 仍 false)。"""
    _clear(env)
    charter_llm.responses.append((200, _chat("## 这是谁的库\n我是一名软件工程师。")))
    resp = env.client.post(
        "/api/charter/generate",
        json={
            "direction": "软件工程本科生",
            "purpose": "课设、答辩",
            "collect": ["讲清概念的笔记", "技术文档"],
            "reject": ["单据表格", "空文件"],
            "unsure": "collect_low",
            "extra": "关注前端技术栈",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "软件工程师" in body["charter"]
    # 不落库:GET 仍然 has_charter=false
    assert env.client.get("/api/charter").json()["has_charter"] is False
    # 只调了 1 次 LLM
    assert len(charter_llm.transports[-1].calls) == 1


def test_generate_prompt_contains_answers(
    env: SimpleNamespace, charter_llm: SimpleNamespace
) -> None:
    """发给 LLM 的 prompt 里包含问卷答案的关键内容。"""
    charter_llm.responses.append((200, _chat("charter text")))
    env.client.post(
        "/api/charter/generate",
        json={
            "direction": "数据科学方向",
            "purpose": "做项目",
            "collect": ["数据集说明"],
            "reject": ["日志文件"],
            "unsure": "reject",
            "extra": "特别在意数据质量",
        },
    )
    raw_body = charter_llm.transports[-1].calls[0]["body"]
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode("utf-8")
    parsed = json.loads(raw_body)
    messages_text = " ".join(m["content"] for m in parsed["messages"])
    assert "数据科学方向" in messages_text
    assert "数据集说明" in messages_text
    assert "日志文件" in messages_text
    assert "数据质量" in messages_text
