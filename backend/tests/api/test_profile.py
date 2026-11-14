"""GET /api/profile 个人中心聚合端点测试。

纯读聚合:用户信息 / 使用统计 / 章程预览 / 配置掩码。三个必须钉住的口径:
1. 掩码与设置页一致——密钥全文不出现在响应任何位置;
2. 统计只数自己的(owner 隔离),notes 只数 active;
3. 章程预览截断 300 字,完整内容仍走 GET /api/charter。

开发库里 owner=1 本来就有真实数据 → 统计一律用**前后差值**断言,不假设库是空的。
种子走 repo(test_note_delete 同款);env 外层事务回滚,不污染开发库。
"""

from __future__ import annotations

import uuid
from functools import partial
from types import SimpleNamespace
from typing import Any

from app.repo.user import UserRepo

MARKER = "profile-test"


def _noauth(env: SimpleNamespace) -> None:
    env.client.headers.pop("Authorization", None)


def _stats(env: SimpleNamespace) -> dict[str, int]:
    return env.client.get("/api/profile").json()["stats"]


async def _seed(factory: Any, *, notes: int, archived: int, sessions: int, msgs: int) -> None:
    """种笔记/会话/消息;archived 的笔记不该被统计进去。"""
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        for i in range(notes):
            await repos.notes.create(
                source_id=None,
                title=f"{MARKER}-note-{i}",
                doc={"type": "doc", "content": [{"type": "p", "text": MARKER}]},
            )
        for i in range(archived):
            note = await repos.notes.create(
                source_id=None,
                title=f"{MARKER}-archived-{i}",
                doc={"type": "doc", "content": [{"type": "p", "text": MARKER}]},
            )
            await repos.notes.update(note.id, status="archived")
        for _s in range(sessions):
            row = await repos.qa.create_session(title=f"{MARKER}-session")
            for _m in range(msgs):
                await repos.qa.add_message(row.id, role="user", content=f"{MARKER}-msg")
        await session.commit()


async def _seed_as_other(factory: Any, other_id: int) -> None:
    """以另一个账号的身份种数据(验证 owner 作用域)。"""
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, other_id)
        await repos.notes.create(
            source_id=None,
            title=f"{MARKER}-other-note",
            doc={"type": "doc", "content": [{"type": "p", "text": MARKER}]},
        )
        await repos.qa.create_session(title=f"{MARKER}-other-session")
        await session.commit()


async def _make_other_user(factory: Any) -> int:
    """直接建另一个账号,拿它的 id(种隔离对照数据用)。"""
    async with factory() as session:
        user = await UserRepo(session).create(
            username=f"{MARKER}-{uuid.uuid4().hex[:10]}", password_hash="x"
        )
        await session.commit()
        return user.id


def test_requires_token(env: SimpleNamespace) -> None:
    _noauth(env)
    assert env.client.get("/api/profile").status_code == 401


def test_user_info(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/profile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 聚合四块,不多不少(多出的字段可能是把密钥之类带出来的信号)
    assert set(body.keys()) == {"user", "stats", "charter", "settings"}
    assert body["user"]["id"] == env.owner_id
    assert isinstance(body["user"]["username"], str) and body["user"]["username"]
    assert body["user"]["created_at"]  # ISO 时间串,注册时间


def test_stats_counts_delta_and_active_only(env: SimpleNamespace) -> None:
    before = _stats(env)
    env.client.portal.call(
        partial(_seed, env.shared["factory"], notes=2, archived=1, sessions=2, msgs=3)
    )
    after = _stats(env)

    assert after["notes"] - before["notes"] == 2  # archived 那条不数
    assert after["sources"] - before["sources"] == 0  # 笔记种子不建 source
    assert after["qa_sessions"] - before["qa_sessions"] == 2
    assert after["qa_messages"] - before["qa_messages"] == 6  # 2 会话 × 3 条


def test_owner_isolated_counts(env: SimpleNamespace) -> None:
    """另一个账号种的数据不进本账号的统计(owner 作用域)。"""
    other_id = env.client.portal.call(_make_other_user, env.shared["factory"])
    before = _stats(env)
    env.client.portal.call(_seed_as_other, env.shared["factory"], other_id)
    after = _stats(env)

    assert after["notes"] == before["notes"]
    assert after["qa_sessions"] == before["qa_sessions"]
    assert after["qa_messages"] == before["qa_messages"]


def test_charter_preview_truncated_then_cleared(env: SimpleNamespace) -> None:
    long_text = "章" * 400
    assert env.client.put("/api/charter", json={"charter": long_text}).status_code == 200

    body = env.client.get("/api/profile").json()["charter"]
    assert body["has_charter"] is True
    assert body["preview"] is not None
    assert len(body["preview"]) == 301  # 300 字 + 截断「…」
    assert body["preview"].endswith("…")

    # 完整内容仍由 GET /api/charter 提供,预览不替代全文
    full = env.client.get("/api/charter").json()["charter"]
    assert len(full) == 400

    # 清空 → 回退内置默认口径:has_charter=false、preview=null
    assert env.client.put("/api/charter", json={"charter": ""}).status_code == 200
    body = env.client.get("/api/profile").json()["charter"]
    assert body["has_charter"] is False
    assert body["preview"] is None


def test_settings_masked_and_no_key_leak(env: SimpleNamespace) -> None:
    secret = "sk-profile-secret-1234"
    resp = env.client.put(
        "/api/settings",
        json={
            "llm_api_key": secret,
            "llm_model": "profile-test-model",
            "tavily_api_key": "tvly-9876abcd",
        },
    )
    assert resp.status_code == 200, resp.text

    profile = env.client.get("/api/profile").json()
    assert profile["settings"] == env.client.get("/api/settings").json()  # 六字段同口径
    assert profile["settings"]["api_key_tail"] == "1234"
    assert profile["settings"]["tavily_tail"] == "abcd"
    # 全文密钥不出现在响应的任何位置
    assert secret not in env.client.get("/api/profile").text
