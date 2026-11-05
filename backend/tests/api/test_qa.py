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
        search_calls=[],  # fake_search 收到的查询(联网兜底)
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


def test_chat_creates_session_and_persists(env: SimpleNamespace, qa_setup: SimpleNamespace) -> None:
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
    qa_setup.responses.append((200, _chat("Promise 是异步操作的结果对象,手写要点如下。[1]")))
    first = env.client.post("/api/qa/chat", json={"question": "什么是 Promise，怎么手写"}).json()
    sid = first["session_id"]

    # 追问:先改写(「它」→「手写实现的 Promise」)再用改写词检索,最后回答
    qa_setup.responses.append((200, _chat("手写实现的 Promise 有哪几种状态")))
    qa_setup.responses.append(
        (200, _chat("手写实现的 Promise 有三种状态:PENDING/FULFILLED/REJECTED。[1]", total=42))
    )
    second = env.client.post("/api/qa/chat", json={"session_id": sid, "question": "它有哪几种状态"})
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
    second = env.client.post("/api/qa/chat", json={"session_id": sid, "question": "它有哪几种状态"})
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


def test_summarizer_creates_import_session(
    env: SimpleNamespace, qa_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阶段3:导入完成 → 摘要 → 自动建「新导入:xxx」会话(首条 assistant 消息)。"""
    from app.repo.library import LibraryRepo
    from app.service.qa import summarizer as summarizer_module
    from app.service.qa.summarizer import summarize_import

    # 假笔记读取(不种 note 行):monkeypatch 掉 LibraryRepo 的两个读取方法
    async def fake_get(self, note_id):  # noqa: ANN001
        return SimpleNamespace(id=note_id, title="Agent 应用开发学习指南", doc={})

    async def fake_parents(self, note_id):  # noqa: ANN001
        return [
            SimpleNamespace(
                text="Agent 五阶段:基础认知→Function Calling→记忆→协作→部署。",
                token_count=30,
            )
        ]

    monkeypatch.setattr(LibraryRepo, "get_active_note", fake_get)
    monkeypatch.setattr(LibraryRepo, "list_parent_chunks", fake_parents)
    # session_factory 指到 env 的事务工厂(回滚不污染)
    monkeypatch.setattr(summarizer_module, "session_factory", env.shared["factory"])

    transport = FakeTransport(
        [(200, _chat("这篇笔记讲 Agent 开发五阶段……建议你问:①最简 Agent 类 ②协调者模式"))],
        [],
    )
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-qa-SECRET",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )

    async def _run():
        return await summarize_import(999, "Agent 指南.md", client=client)

    created = env.client.portal.call(_run)
    assert created is not None
    assert created.title.startswith("新导入:Agent 指南")

    detail = env.client.get(f"/api/qa/sessions/{created.id}").json()
    assert [m["role"] for m in detail["messages"]] == ["assistant"]
    assert detail["messages"][0]["content"].startswith("这篇笔记")


def test_summarizer_note_missing_returns_none(
    env: SimpleNamespace, qa_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.service.qa import summarizer as summarizer_module
    from app.service.qa.summarizer import summarize_import

    monkeypatch.setattr(summarizer_module, "session_factory", env.shared["factory"])

    async def _run():
        return await summarize_import(999999, "不存在.md")

    created = env.client.portal.call(_run)
    assert created is None


def test_chat_web_fallback_when_kb_misses(
    env: SimpleNamespace, qa_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阶段5 工具链:库内没命中 + 配了 Tavily → 自动搜网,回答带 url 出处。"""

    qa_setup.hits = []  # 库内没命中

    async def fake_tavily_key(session, owner_id):  # noqa: ANN001
        return "tvly-test"

    def fake_search(query, *, api_key, max_results=5):  # noqa: ANN001
        qa_setup.search_calls.append(query)
        return [
            {
                "title": "Promise MDN",
                "url": "https://example.com/promise",
                "content": "Promise 三种状态……",
            }
        ]

    monkeypatch.setattr(qa_module, "resolve_tavily_key", fake_tavily_key)
    monkeypatch.setattr(qa_module, "search_web", fake_search)
    qa_setup.responses.append(
        (200, _chat("根据网络搜索:Promise 有三种状态……(来源见 [W1])", total=30))
    )

    resp = env.client.post("/api/qa/chat", json={"question": "什么是 WebGPU"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_web"] is True
    assert qa_setup.queries[-1] == "什么是 WebGPU"
    assert body["assistant_message"]["citations"][0]["url"] == "https://example.com/promise"
    assert body["assistant_message"]["citations"][0]["note_id"] is None
    assert qa_setup.search_calls == ["什么是 WebGPU"]


def test_save_note_writes_file_and_enqueues_import(
    env: SimpleNamespace,
    qa_setup: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """阶段4(D2=B):会话 → LLM 整理 → 落 notes_out/ → no_llm 入队。"""
    qa_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
    first = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()
    sid = first["session_id"]

    monkeypatch.setattr(qa_module, "NOTES_OUT_DIR", tmp_path / "notes_out")
    import app.worker.worker as worker_module

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path / "staging")
    enqueued: list[dict] = []

    async def fake_enqueue(
        file_path, *, dedupe_key, no_llm=False, owner_id=1
    ):  # noqa: ANN001
        enqueued.append({"file": file_path, "no_llm": no_llm})
        return dedupe_key

    monkeypatch.setattr(worker_module, "enqueue_import", fake_enqueue)
    qa_setup.responses.append((200, _chat("# 闭包笔记\n\n要点……")))

    resp = env.client.post(f"/api/qa/sessions/{sid}/save-note")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["import_enqueued"] is True
    assert enqueued and enqueued[0]["no_llm"] is True
    files = list((tmp_path / "notes_out").glob("*.md"))
    assert len(files) == 1
    assert "闭包" in files[0].read_text(encoding="utf-8")

    # 不存在的会话 → 404
    assert env.client.post("/api/qa/sessions/999999/save-note").status_code == 404


def _make_enqueue_recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[dict]:
    monkeypatch.setattr(qa_module, "NOTES_OUT_DIR", tmp_path / "notes_out")
    import app.worker.worker as worker_module

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path / "staging")
    enqueued: list[dict] = []

    async def fake_enqueue(
        file_path, *, dedupe_key, no_llm=False, owner_id=1
    ):  # noqa: ANN001
        enqueued.append({"file": file_path, "no_llm": no_llm})
        return dedupe_key

    monkeypatch.setattr(worker_module, "enqueue_import", fake_enqueue)
    return enqueued


def test_note_plan_returns_topics(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    """note-plan:LLM 返回两个主题 → 原样返回,建议 split。"""
    qa_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
    sid = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()["session_id"]

    plan_json = json.dumps(
        {
            "topics": [
                {"title": "闭包原理", "summary": "定义与条件", "msg_start": 0, "msg_end": 1},
            ],
            "suggestion": "single",
        },
        ensure_ascii=False,
    )
    qa_setup.responses.append((200, _chat(plan_json)))
    resp = env.client.post(f"/api/qa/sessions/{sid}/note-plan")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["suggestion"] == "single"
    assert body["topics"][0]["title"] == "闭包原理"
    assert body["topics"][0]["id"] == 0

    # 消息不足的会话 → 400
    qa_setup.responses.append((200, _chat("你好")))
    env.client.post("/api/qa/chat", json={"question": "你好"})
    # 404 分支:会话不存在
    assert env.client.post("/api/qa/sessions/999999/note-plan").status_code == 404


def test_note_plan_garbage_falls_back_to_single(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    """LLM 返回垃圾 → 回退整段一篇(single),不报错。"""
    qa_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
    sid = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()["session_id"]
    qa_setup.responses.append((200, _chat("抱歉我不能输出 JSON")))
    resp = env.client.post(f"/api/qa/sessions/{sid}/note-plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestion"] == "single"
    assert len(body["topics"]) == 1


def test_save_note_split_generates_one_file_per_topic(
    env: SimpleNamespace,
    qa_setup: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """split 模式:两主题 → 两次整理 LLM 调用 → 两篇文件两次入队。"""
    qa_setup.responses.append((200, _chat("先聊数据库 [1]")))
    sid = env.client.post("/api/qa/chat", json={"question": "讲讲数据库"}).json()["session_id"]
    # 第二问有历史 → 先改写后回答,队列要两帧
    qa_setup.responses.append((200, _chat("硬件知识")))
    qa_setup.responses.append((200, _chat("再聊硬件 [1]")))
    env.client.post("/api/qa/chat", json={"question": "再问硬件", "session_id": sid})

    enqueued = _make_enqueue_recorder(monkeypatch, tmp_path)

    plan_json = json.dumps(
        {
            "topics": [
                {"title": "数据库索引", "summary": "B+ 树", "msg_start": 0, "msg_end": 1},
                {"title": "SSD 硬件", "summary": "选购要点", "msg_start": 2, "msg_end": 3},
            ],
            "suggestion": "split",
        },
        ensure_ascii=False,
    )
    # 队列按序消费:先 plan,再每主题一次整理
    qa_setup.responses.append((200, _chat(plan_json)))
    qa_setup.responses.append((200, _chat("# 数据库索引\n\nB+ 树…")))
    qa_setup.responses.append((200, _chat("# SSD 硬件\n\n选购…")))

    resp = env.client.post(
        f"/api/qa/sessions/{sid}/save-note", json={"mode": "split"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["notes"]) == 2
    assert body["notes"][0]["title"] == "数据库索引"
    assert body["notes"][1]["title"] == "SSD 硬件"
    assert len(enqueued) == 2
    files = list((tmp_path / "notes_out").glob("*.md"))
    assert len(files) == 2
    contents = " ".join(f.read_text(encoding="utf-8") for f in files)
    assert "数据库索引" in contents and "SSD" in contents


def test_save_note_instruction_reaches_prompt(
    env: SimpleNamespace,
    qa_setup: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """instruction(用户自由输入)必须进到整理 prompt 里。"""
    qa_setup.responses.append((200, _chat("闭包是函数+捕获的外层变量。[1]")))
    sid = env.client.post("/api/qa/chat", json={"question": "闭包是什么"}).json()["session_id"]

    enqueued = _make_enqueue_recorder(monkeypatch, tmp_path)
    qa_setup.responses.append((200, _chat("# 只要代码\n\n```js\nconst x = 1\n```")))

    resp = env.client.post(
        f"/api/qa/sessions/{sid}/save-note",
        json={"mode": "single", "instruction": "只要代码示例,不要解释"},
    )
    assert resp.status_code == 200, resp.text
    # 最后一次 LLM 调用的 system prompt 应含用户要求
    last_body = qa_setup.transports[-1].calls[-1]["body"]
    if isinstance(last_body, bytes):
        last_body = last_body.decode("utf-8")
    assert "只要代码示例,不要解释" in last_body
    assert len(enqueued) == 1


def test_chat_with_referenced_note_puts_it_in_prompt(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    """引用笔记 = 告诉 AI「在问什么」:正文进 <referenced_notes>,检索照跑。"""
    # 造一篇带父块的笔记:用夹具同一事务的 factory,chat 请求才能读到
    from app.models import Chunk
    from app.repo import Repos

    async def _seed() -> int:
        async with env.shared["factory"]() as session:
            repos = Repos(session, env.owner_id)
            note = await repos.notes.create(
                source_id=None,
                title="闭包完全指南",
                doc={"type": "doc", "content": []},
                quality="core",
            )
            session.add(
                Chunk(
                    note_id=note.id,
                    ord=0,
                    is_parent=True,
                    heading_path="闭包完全指南",
                    text="闭包=函数+它捕获的外层变量。REF_MARKER_XYZ",
                    token_count=10,
                    meta={},
                )
            )
            await session.commit()
            return note.id

    note_id = env.client.portal.call(_seed)

    qa_setup.responses.append((200, _chat("按你引用的笔记讲讲 [1]")))
    resp = env.client.post(
        "/api/qa/chat", json={"question": "闭包是什么", "note_ids": [note_id]}
    )
    assert resp.status_code == 200, resp.text
    # 引用笔记正文进了 prompt
    last_body = qa_setup.transports[-1].calls[-1]["body"]
    if isinstance(last_body, bytes):
        last_body = last_body.decode("utf-8")
    assert "REF_MARKER_XYZ" in last_body
    assert "<referenced_notes>" in last_body
    # 检索照跑(fake_retrieve 记录了检索词)
    assert qa_setup.queries == ["闭包是什么"]


def test_chat_with_bogus_note_ids_ignored(
    env: SimpleNamespace, qa_setup: SimpleNamespace
) -> None:
    """非法/别人的 note_id 静默忽略,不影响问答。"""
    qa_setup.responses.append((200, _chat("答 [1]")))
    resp = env.client.post(
        "/api/qa/chat", json={"question": "讲讲", "note_ids": [999999, -1]}
    )
    assert resp.status_code == 200, resp.text
    last_body = qa_setup.transports[-1].calls[-1]["body"]
    if isinstance(last_body, bytes):
        last_body = last_body.decode("utf-8")
    assert "<referenced_notes>" not in last_body


def test_chat_no_kb_marker_triggers_web_search(
    env: SimpleNamespace, qa_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 判定笔记答不了([NO_KB]) → 自动联网二次回答,不让用户吃闭门羹。

    复现主人实测:「关系型数据库」被发票笔记里的「数据库」字面误判为相关,
    旧版只能拒答;现在靠 LLM 自己判语义,联网给出正面解答。
    """
    async def fake_tavily_key(*_a, **_kw):  # noqa: ANN001
        return "tvly-fake"

    monkeypatch.setattr(qa_module, "resolve_tavily_key", fake_tavily_key)

    search_calls: list[str] = []

    def fake_search(query, *, api_key):  # noqa: ANN001
        search_calls.append(query)
        return [
            {"title": "闭包本质详解", "url": "https://closure.example", "content": "闭包的本质是…"}
        ]

    monkeypatch.setattr(qa_module, "search_web", fake_search)

    # 场景:检索命中闭包笔记(字面相关),但 LLM 判定答不了概念追问
    # → [NO_KB] → 联网二次回答
    qa_setup.responses.append((200, _chat("[NO_KB]\n闭包的本质是函数对外层变量的引用机制…")))
    qa_setup.responses.append((200, _chat("闭包的本质是作用域引用。[W1]")))
    resp = env.client.post("/api/qa/chat", json={"question": "闭包的本质是什么"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_web"] is True
    assert "作用域引用" in body["assistant_message"]["content"]
    assert search_calls, "应该发起联网搜索"
    assert "[NO_KB]" not in body["assistant_message"]["content"]  # 标记不外泄


def test_chat_no_kb_marker_without_tavily_answers_from_own_knowledge(
    env: SimpleNamespace, qa_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 Tavily → 用 LLM 已给出的自身知识回答,带来源标注,不再拒答。"""

    async def fake_tavily_none(*_a, **_kw):  # noqa: ANN001
        return None

    monkeypatch.setattr(qa_module, "resolve_tavily_key", fake_tavily_none)

    qa_setup.responses.append(
        (200, _chat("[NO_KB]\n关系型数据库是建立在关系模型上的数据库,数据按表组织。"))
    )
    resp = env.client.post("/api/qa/chat", json={"question": "什么叫做关系型数据库"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_web"] is False
    content = body["assistant_message"]["content"]
    assert "AI 自身的知识回答" in content
    assert "关系模型" in content
    assert "[NO_KB]" not in content


class TestHasRelevantHit:
    """相关性检查:决定库内命中算不算相关(不相关才联网兜底)。"""

    def _hit(self, text: str) -> SimpleNamespace:
        return SimpleNamespace(parent_text=text)

    def test_mixed_chinese_english_no_space_matches(self) -> None:
        """中英连写(无空格)的查询要切出英文段匹配——修「给我讲讲关于workflow」误判不相关。"""
        hit = self._hit("一张「Workflow 工作流 · 学习笔记」长图,讲工作流的核心概念")
        assert qa_module._has_relevant_hit([hit], "给我讲讲关于workflow") is True

    def test_english_term_in_chinese_text_matches(self) -> None:
        assert qa_module._has_relevant_hit([self._hit("Promise 有三种状态")], "讲讲promise") is True

    def test_english_term_not_in_text_not_relevant(self) -> None:
        """查询的英文词不在命中正文里 → 不相关(该联网)。"""
        assert qa_module._has_relevant_hit([self._hit("工作流的核心概念")], "ReAct是什么") is False

    def test_pure_chinese_2gram_matches(self) -> None:
        assert qa_module._has_relevant_hit(
            [self._hit("闭包是函数加捕获的外层变量")], "闭包是什么"
        ) is True

    def test_short_letter_run_not_specific(self) -> None:
        """单个字母(如「关于a」)不算英文专有词,走中文滑窗;这里「关于」不在正文→不相关。"""
        assert qa_module._has_relevant_hit([self._hit("工作流核心概念")], "关于a") is False
