"""认证 API 测试:注册 / 登录 / me / 401 / 用户隔离。

env 夹具已给 TestClient 装了默认 token(测试账号 id=1),
这里注册/登录类用例自己清掉该头,避免干扰。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace


def _uniq(prefix: str = "u") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _noauth(env: SimpleNamespace) -> None:
    env.client.headers.pop("Authorization", None)


def test_register_returns_token_and_user(env: SimpleNamespace) -> None:
    _noauth(env)
    name = _uniq()
    resp = env.client.post(
        "/api/auth/register", json={"username": name, "password": "secret12345"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == name
    assert isinstance(body["user"]["id"], int)


def test_register_duplicate_username_409(env: SimpleNamespace) -> None:
    _noauth(env)
    name = _uniq()
    first = env.client.post(
        "/api/auth/register", json={"username": name, "password": "secret12345"}
    )
    assert first.status_code == 200, first.text
    dup = env.client.post(
        "/api/auth/register", json={"username": name, "password": "other12345"}
    )
    assert dup.status_code == 409
    assert "已被占用" in dup.json()["detail"]


def test_login_success_and_wrong_password(env: SimpleNamespace) -> None:
    _noauth(env)
    name = _uniq()
    env.client.post("/api/auth/register", json={"username": name, "password": "secret12345"})

    ok = env.client.post("/api/auth/login", json={"username": name, "password": "secret12345"})
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == name

    bad = env.client.post("/api/auth/login", json={"username": name, "password": "wrongpass123"})
    assert bad.status_code == 401
    # 不给「用户不存在」和「密码错」不同文案,避免枚举用户名
    assert bad.json()["detail"] == "用户名或密码错误"


def test_login_unknown_user_same_error(env: SimpleNamespace) -> None:
    _noauth(env)
    resp = env.client.post(
        "/api/auth/login", json={"username": _uniq("nobody"), "password": "secret12345"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"


def test_me_requires_token(env: SimpleNamespace) -> None:
    _noauth(env)
    resp = env.client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_token(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/auth/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == env.owner_id


def test_invalid_token_401(env: SimpleNamespace) -> None:
    env.client.headers["Authorization"] = "Bearer not-a-real-token"
    resp = env.client.get("/api/auth/me")
    assert resp.status_code == 401


def test_protected_endpoints_require_auth(env: SimpleNamespace) -> None:
    """业务接口没 token 一律 401(healthz 除外,由别处保证)。"""
    _noauth(env)
    for path in [
        "/api/notes",
        "/api/sources",
        "/api/qa/sessions",
        "/api/settings",
        "/api/charter",
        "/api/profile",
    ]:
        resp = env.client.get(path)
        assert resp.status_code == 401, f"{path} 未拦截:{resp.status_code}"


def test_two_users_are_isolated(env: SimpleNamespace) -> None:
    """两个账号的会话/配置互不可见。"""
    _noauth(env)
    a_name = _uniq("a")
    a = env.client.post(
        "/api/auth/register", json={"username": a_name, "password": "secret12345"}
    ).json()
    env.client.headers["Authorization"] = f"Bearer {a['access_token']}"
    # A 写一份自己的章程(不依赖 LLM)
    a_charter = f"# {a_name} 的章程\n只收测试相关的内容。"
    assert env.client.put("/api/charter", json={"charter": a_charter}).status_code == 200

    # B 注册后:看不到 A 的章程
    _noauth(env)
    b_name = _uniq("b")
    b = env.client.post(
        "/api/auth/register", json={"username": b_name, "password": "secret12345"}
    ).json()
    env.client.headers["Authorization"] = f"Bearer {b['access_token']}"

    b_charter = env.client.get("/api/charter").json()
    assert b_charter["has_charter"] is False
    assert b_charter["charter"] is None

    # B 的会话列表是空的(不会看到 A 的)
    assert env.client.get("/api/qa/sessions").json()["total"] == 0

    # B 写了章程后,A 的仍然是原来那份(各自独立)
    env.client.put("/api/charter", json={"charter": "# B 的章程"})
    env.client.headers["Authorization"] = f"Bearer {a['access_token']}"
    assert env.client.get("/api/charter").json()["charter"] == a_charter
