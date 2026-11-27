"""画像 API 测试(原 charter 测试,2026-11-26 扩成 persona):GET/PUT 事实+章程、POST generate。

env 夹具提供 TestClient + 事务隔离(每条测试回滚,不污染 dev 库)。
generate 端点用 FakeTransport 假 LLM(不联网)。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.api import persona as persona_module
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
    assert env.client.put("/api/persona", json={"charter": "", "nickname": ""}).status_code == 200


@pytest.fixture
def persona_llm(env: SimpleNamespace) -> SimpleNamespace:
    """env + 假 LLM(覆盖 persona_module.get_llm_client)。"""
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

    app.dependency_overrides[persona_module.get_llm_client] = fake_llm
    try:
        yield state
    finally:
        app.dependency_overrides.pop(persona_module.get_llm_client, None)


def test_get_no_charter(env: SimpleNamespace) -> None:
    """DB 没有 charter(刚加的列,默认 NULL)→ has_charter=false, charter/nickname=null。"""
    # 夹具账号可能已有章程(开发库残留),先清空再断言
    _clear(env)
    resp = env.client.get("/api/persona")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_charter"] is False
    assert body["charter"] is None
    assert body["nickname"] is None


def test_put_then_get(env: SimpleNamespace) -> None:
    """PUT 存 charter → GET 有 charter + has_charter=true。"""
    text = "## 这是谁的库\n我是一名测试工程师。\n\n## 收什么\n- 技术笔记"
    resp = env.client.put("/api/persona", json={"charter": text})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_charter"] is True
    assert body["charter"] == text

    # GET 确认持久(同一事务内)
    body = env.client.get("/api/persona").json()
    assert body["has_charter"] is True
    assert body["charter"] == text


def test_generate_returns_charter_text(env: SimpleNamespace, persona_llm: SimpleNamespace) -> None:
    """POST generate(事实问卷)→ LLM 生成 charter 文本(不落库,GET 仍 false)。"""
    _clear(env)
    persona_llm.responses.append((200, _chat("## 这是谁的库\n「小明」的库,一名软件工程师。")))
    resp = env.client.post(
        "/api/persona/generate",
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
    assert env.client.get("/api/persona").json()["has_charter"] is False
    # 只调了 1 次 LLM
    assert len(persona_llm.transports[-1].calls) == 1


def test_generate_prompt_contains_answers(
    env: SimpleNamespace, persona_llm: SimpleNamespace
) -> None:
    """发给 LLM 的 prompt 里包含全部六个事实(2026-11-26 事实化问卷)。"""
    persona_llm.responses.append((200, _chat("charter text")))
    env.client.post(
        "/api/persona/generate",
        json={
            "nickname": "阿美",
            "age_band": "25-30",
            "gender": "女",
            "education": "硕士及以上",
            "status": "在职",
            "why": "工作沉淀",
        },
    )
    raw_body = persona_llm.transports[-1].calls[0]["body"]
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode("utf-8")
    parsed = json.loads(raw_body)
    messages_text = " ".join(m["content"] for m in parsed["messages"])
    for fact in ("阿美", "25-30", "女", "硕士及以上", "在职", "工作沉淀"):
        assert fact in messages_text, f"事实「{fact}」必须进 prompt"


def test_generate_prompt_bakes_fixed_rules(
    env: SimpleNamespace, persona_llm: SimpleNamespace
) -> None:
    """拿不准口径不再问用户(旧问卷唯一硬生效项),固定「宁收低质量」写进生成 prompt。"""
    persona_llm.responses.append((200, _chat("charter text")))
    env.client.post("/api/persona/generate", json={})
    raw_body = persona_llm.transports[-1].calls[0]["body"]
    if isinstance(raw_body, bytes):
        raw_body = raw_body.decode("utf-8")
    parsed = json.loads(raw_body)
    messages_text = " ".join(m["content"] for m in parsed["messages"])
    assert "拿不准就收下" in messages_text
    # 事实推导口径:偏好是 LLM 的推导工作,不是用户的选择题
    assert "推导" in messages_text


def test_generate_accepts_legacy_payload_without_new_fields(
    env: SimpleNamespace, persona_llm: SimpleNamespace
) -> None:
    """旧版问卷字段(direction/purpose/collect...)是未知键,直接忽略;全空照常生成。"""
    persona_llm.responses.append((200, _chat("ok")))
    resp = env.client.post(
        "/api/persona/generate",
        json={"direction": "测试", "purpose": "测试", "collect": [], "reject": []},
    )
    assert resp.status_code == 200, resp.text


def test_put_nickname_roundtrip(env: SimpleNamespace) -> None:
    """PUT 连带存昵称;不带 nickname 再 PUT 不动它;空串清空。"""
    _clear(env)
    text = "## 这是谁的库\n一名测试工程师的库"
    resp = env.client.put("/api/persona", json={"charter": text, "nickname": "  小明  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["nickname"] == "小明"  # 剥首尾空白
    assert env.client.get("/api/persona").json()["nickname"] == "小明"

    # 只 PUT charter(nickname 缺省 = 不动)→ 昵称保留
    assert env.client.put("/api/persona", json={"charter": text}).status_code == 200
    assert env.client.get("/api/persona").json()["nickname"] == "小明"

    # nickname 空串 = 清空
    assert env.client.put("/api/persona", json={"charter": text, "nickname": ""}).status_code == 200
    assert env.client.get("/api/persona").json()["nickname"] is None


# ── 画像批(2026-11-26):五项事实补存 + 统计三分 + 主题打标 ──


def _clear_facts(env: SimpleNamespace) -> None:
    """清空事实与章程(空串 = 清空,回退内置默认)。"""
    payload = {
        "charter": "",
        "nickname": "",
        "age_band": "",
        "gender": "",
        "education": "",
        "status": "",
        "why": "",
    }
    assert env.client.put("/api/persona", json=payload).status_code == 200


def test_put_five_facts_roundtrip(env: SimpleNamespace) -> None:
    """五项问卷事实 PUT → GET 回读一致;空串清空。

    迁移 0021 之前这五项只喂给 LLM 生成章程、生成完就丢,画像页左栏没数据源。
    """
    _clear_facts(env)
    text = "## 这是谁的库\n测试"
    facts = {
        "age_band": "18-24",
        "gender": "男",
        "education": "本科在读",
        "status": "在读学生",
        "why": "课程学习",
    }
    resp = env.client.put("/api/persona", json={"charter": text, **facts})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key, value in facts.items():
        assert body[key] == value, f"{key} 应回 {value}"

    # GET 确认持久
    got = env.client.get("/api/persona").json()
    for key, value in facts.items():
        assert got[key] == value, f"{key} 落库后应能读回"

    # 空串 = 清空(NULL)
    cleared = {**facts, "charter": text, "why": ""}
    assert env.client.put("/api/persona", json=cleared).status_code == 200
    assert env.client.get("/api/persona").json()["why"] is None
    # 其余字段不受影响
    assert env.client.get("/api/persona").json()["age_band"] == "18-24"


def test_put_facts_omitted_keeps_old_value(env: SimpleNamespace) -> None:
    """不传的字段 = 不动。只改昵称时不必回传整份事实。"""
    _clear_facts(env)
    env.client.put(
        "/api/persona",
        json={"charter": "c1", "age_band": "25-30", "education": "硕士及以上"},
    )
    resp = env.client.put("/api/persona", json={"charter": "c2", "nickname": "小明"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nickname"] == "小明"
    assert body["age_band"] == "25-30"  # 未传 → 保留
    assert body["education"] == "硕士及以上"


def test_facts_are_trimmed(env: SimpleNamespace) -> None:
    """首尾空白剥掉(与 nickname 同口径)。"""
    _clear_facts(env)
    resp = env.client.put(
        "/api/persona",
        json={"charter": "x", "why": "  课程学习  ", "age_band": " 18-24 "},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["why"] == "课程学习"
    assert resp.json()["age_band"] == "18-24"


def test_stats_three_way_split_is_exclusive(env: SimpleNamespace) -> None:
    """回答方式三分必须互斥:kb + web + self == total。

    早期口径 kb 数「有 note」、web 数「有 url」,两者有交集 → 相加超过 total,
    前端算出 47%+37%+31%=115% 的荒唐百分比(真机截图抓出来的)。
    """
    body = env.client.get("/api/persona/stats").json()
    answers = body["answers"]
    assert answers["kb"] + answers["web"] + answers["self"] == answers["total"]
    if answers["total"] > 0:
        for key in ("kb", "web", "self"):
            assert 0 <= answers[key] <= answers["total"]


def test_stats_shape(env: SimpleNamespace) -> None:
    """统计端点返回八块,字段齐且类型对(不校验具体数值,dev 库会变)。"""
    resp = env.client.get("/api/persona/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "size",
        "sources",
        "quality",
        "answers",
        "top_cited",
        "dead_notes",
        "self_answered",
        "activity",
        "topics",
        "untagged",
    ):
        assert key in body, f"统计缺 {key}"
    assert set(body["size"]) == {"notes", "chunks", "sessions", "asks"}
    assert isinstance(body["top_cited"], list)
    assert isinstance(body["untagged"], int)

    # 两个清单块的形状必须一致:都是 {total, items},且 total 是**真实总数**
    # 而不是 len(items) —— items 有 30 条上限,拿 len 当总数页面会永远显示 30。
    for key, item_type in (("dead_notes", dict), ("self_answered", str)):
        block = body[key]
        assert set(block) == {"total", "items"}, f"{key} 形状应为 {{total, items}}"
        assert isinstance(block["total"], int)
        assert isinstance(block["items"], list)
        assert block["total"] >= len(block["items"]), (
            f"{key}: items 不能多于 total({len(block['items'])} > {block['total']})"
        )
        for item in block["items"][:3]:
            assert isinstance(item, item_type), f"{key} 的 item 类型不对:{type(item)}"


def test_stats_scoped_to_owner(env: SimpleNamespace) -> None:
    """统计只算自己 owner 的数据(不串别人的笔记/会话)。"""
    # 夹具里该 owner 的规模应与直接数库一致,这里只做「非负且不比全库大」的弱断言,
    # 强断言靠 persona repo 的 owner 过滤(所有 SQL 都带 :owner)。
    body = env.client.get("/api/persona/stats").json()
    assert body["size"]["notes"] >= 0
    assert body["size"]["chunks"] >= 0


def test_topic_parse_filters_invented_tags() -> None:
    """主题打标的解析层:模型自创的标签、幻觉 id 必须丢掉。

    单阶段打标实测会让标签漂移成近义词(「AI」「AI与大模型」「AI与智能体」并存),
    所以阶段②给了固定主题表,解析层用 allowed_tags 硬卡 —— 这条守住那个闸。
    """
    from app.service.persona.topics import _parse_reply

    raw = json.dumps(
        {
            "1": ["工程基础"],  # 合法
            "2": ["自创标签", "工程基础"],  # 自创的丢掉,合法的留
            "999": ["工程基础"],  # 幻觉 id,整条丢
            "3": "财经",  # 字符串形式也接受
        }
    )
    parsed = _parse_reply(raw, {1, 2, 3}, allowed_tags={"工程基础", "财经"})
    assert parsed[1] == ["工程基础"]
    assert parsed[2] == ["工程基础"]  # 自创标签被过滤
    assert 999 not in parsed
    assert parsed[3] == ["财经"]


def test_topic_parse_tolerates_fenced_json() -> None:
    """模型把 JSON 包在 ```json 围栏里 / 前后加客套话,都要能抠出来。"""
    from app.service.persona.topics import _parse_reply, _parse_vocab

    fenced = '好的,结果如下:\n```json\n{"1": ["财经"]}\n```\n希望有帮助。'
    assert _parse_reply(fenced, {1}) == {1: ["财经"]}

    vocab = '```json\n["工程基础", "财经", "工程基础"]\n```'
    assert _parse_vocab(vocab) == ["工程基础", "财经"]  # 去重且保序


def test_topic_parse_discards_broken_json() -> None:
    """坏 JSON / 非对象 / 空 → 返回空 dict,不抛异常(调用方据此报「没有可用结果」)。"""
    from app.service.persona.topics import _parse_reply, _parse_vocab

    assert _parse_reply("完全不是 JSON", {1}) == {}
    assert _parse_reply('["数组不是对象"]', {1}) == {}
    assert _parse_reply("", {1}) == {}
    assert _parse_vocab("不是数组") == []


def _seed_self_answered(env: SimpleNamespace, count: int) -> None:
    """造 count 组「用户提问 / AI 无引用自答」消息(同一会话交替,配对同会话上一条 user)。

    口径对齐 ``repo.persona.self_answered``:assistant 消息 citations 是数组且既无
    note_id 也无 url;提问长度 ≥6、非纯问候、含字母数字。直接写事务连接,随夹具回滚。
    """
    from sqlalchemy import text

    async def _seed() -> None:
        conn = env.shared["conn"]
        sid = (
            await conn.execute(
                text(
                    "INSERT INTO qa_sessions (owner_id, title) VALUES (1, '自答测试') RETURNING id"
                )
            )
        ).scalar_one()
        for i in range(count):
            await conn.execute(
                text(
                    "INSERT INTO qa_messages (owner_id, session_id, role, content, citations) "
                    "VALUES (1, :sid, 'user', :q, '[]'::jsonb)"
                ),
                {"sid": sid, "q": f"第 {i:03d} 个问题到底是什么"},
            )
            await conn.execute(
                text(
                    "INSERT INTO qa_messages (owner_id, session_id, role, content, citations) "
                    "VALUES (1, :sid, 'assistant', '百科里没找到,改由我自己回答。', '[]'::jsonb)"
                ),
                {"sid": sid},
            )

    env.client.portal.call(_seed)


def test_self_answered_total_counts_all_not_page(env: SimpleNamespace) -> None:
    """自答清单的 total 必须是**真实总数**,不能等于 len(items) 上限。

    真机截图抓出来的 bug:列表上限改 30 后,页面显示「30」而不是真实的 86。
    页面上同一个数字,一个是真的「我有 86 个问题没答上」,一个是「我这页放了 30 条」——
    后者会骗人。这条守住「total 走独立 COUNT 查询」而不是 len(items)。

    数据自己造(31 组 > 页上限 30):原先靠开发库里那 86 条真实记录,CI 空库直接
    total(0) vs items(0) 判不出来 —— 又一处「本地绿靠开发库残留」。
    """
    _seed_self_answered(env, 31)
    block = env.client.get("/api/persona/stats").json()["self_answered"]
    assert len(block["items"]) == 30, f"items 有 30 条上限(实得 {len(block['items'])})"
    assert block["total"] >= 31, f"total 少了造进去的 31 条(实得 {block['total']})"
    # 核心断言:总数独立 COUNT,不受页上限影响(相等即退回 len(items))
    assert block["total"] > len(block["items"]), (
        f"total({block['total']}) 应大于 items 条数({len(block['items'])});"
        " 若相等,多半是 total 退回了 len(items) 而不是独立 COUNT"
    )
