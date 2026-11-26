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
    """把该账号的 charter 和 nickname 清空(UPDATE 空串 = 清空,回退内置默认)。"""
    assert (
        env.client.put("/api/charter", json={"charter": "", "nickname": ""}).status_code == 200
    )


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
    """DB 没有 charter(刚加的列,默认 NULL)→ has_charter=false, charter/nickname=null。"""
    # 夹具账号可能已有章程(开发库残留),先清空再断言
    _clear(env)
    resp = env.client.get("/api/charter")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_charter"] is False
    assert body["charter"] is None
    assert body["nickname"] is None


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
    """POST generate(事实问卷)→ LLM 生成 charter 文本(不落库,GET 仍 false)。"""
    _clear(env)
    charter_llm.responses.append((200, _chat("## 这是谁的库\n「小明」的库,一名软件工程师。")))
    resp = env.client.post(
        "/api/charter/generate",
        json={
            "nickname": "小明",
            "age_band": "18-24",
            "gender": "男",
            "education": "本科在读",
            "status": "在读学生",
            "why": "课程学习+课设沉淀",
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
    """发给 LLM 的 prompt 里包含全部六个事实(2026-11-26 事实化问卷)。"""
    charter_llm.responses.append((200, _chat("charter text")))
    env.client.post(
        "/api/charter/generate",
        json={
            "nickname": "阿美",
            "age_band": "25-30",
            "gender": "女",
            "education": "硕士及以上",
            "status": "在职",
            "why": "工作沉淀",
        },
    )
    raw_body = charter_llm.transports[-1].calls[0]["body"]
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode("utf-8")
    parsed = json.loads(raw_body)
    messages_text = " ".join(m["content"] for m in parsed["messages"])
    for fact in ("阿美", "25-30", "女", "硕士及以上", "在职", "工作沉淀"):
        assert fact in messages_text, f"事实「{fact}」必须进 prompt"


def test_generate_prompt_bakes_fixed_rules(
    env: SimpleNamespace, charter_llm: SimpleNamespace
) -> None:
    """拿不准口径不再问用户(旧问卷唯一硬生效项),固定「宁收低质量」写进生成 prompt。"""
    charter_llm.responses.append((200, _chat("charter text")))
    env.client.post("/api/charter/generate", json={})
    raw_body = charter_llm.transports[-1].calls[0]["body"]
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode("utf-8")
    parsed = json.loads(raw_body)
    messages_text = " ".join(m["content"] for m in parsed["messages"])
    assert "拿不准就收下" in messages_text
    # 事实推导口径:偏好是 LLM 的推导工作,不是用户的选择题
    assert "推导" in messages_text


def test_generate_accepts_legacy_payload_without_new_fields(
    env: SimpleNamespace, charter_llm: SimpleNamespace
) -> None:
    """旧版问卷字段(direction/purpose/collect...)是未知键,直接忽略;全空照常生成。"""
    charter_llm.responses.append((200, _chat("ok")))
    resp = env.client.post(
        "/api/charter/generate",
        json={"direction": "测试", "purpose": "测试", "collect": [], "reject": []},
    )
    assert resp.status_code == 200, resp.text


def test_put_nickname_roundtrip(env: SimpleNamespace) -> None:
    """PUT 连带存昵称;不带 nickname 再 PUT 不动它;空串清空。"""
    _clear(env)
    text = "## 这是谁的库\n一名测试工程师的库"
    resp = env.client.put("/api/charter", json={"charter": text, "nickname": "  小明  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["nickname"] == "小明"  # 剥首尾空白
    assert env.client.get("/api/charter").json()["nickname"] == "小明"

    # 只 PUT charter(nickname 缺省 = 不动)→ 昵称保留
    assert env.client.put("/api/charter", json={"charter": text}).status_code == 200
    assert env.client.get("/api/charter").json()["nickname"] == "小明"

    # nickname 空串 = 清空
    assert env.client.put("/api/charter", json={"charter": text, "nickname": ""}).status_code == 200
    assert env.client.get("/api/charter").json()["nickname"] is None
