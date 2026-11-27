"""B站扫码登录 API 测试：POST 弹窗扫码（mock）落库、GET 登录态、resolve 优先级。

qr_login_bilibili 全 mock——不弹浏览器；DB 走 conftest 的外层事务回滚。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.config import get_settings


def _clear_bili_login(env: SimpleNamespace) -> None:
    """清掉 owner 1 的 SESSDATA（dev 库可能真扫过码），保证断言稳定。"""

    async def _reset() -> None:
        from sqlalchemy import text

        async with env.shared["factory"]() as session:
            await session.execute(
                text("UPDATE app_settings SET bilibili_sessdata = NULL WHERE owner_id = 1")
            )
            await session.commit()

    env.client.portal.call(_reset)


def test_get_login_status_defaults_to_logged_out(env: SimpleNamespace) -> None:
    _clear_bili_login(env)
    resp = env.client.get("/api/bilibili/login")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["logged_in"] is False
    assert body["uid"] == ""
    assert "sessdata" not in body  # SESSDATA 全文永不回显


def test_post_login_persists_and_gets_status(env: SimpleNamespace, monkeypatch) -> None:
    from app.api import bilibili as bili_api
    from app.service.bilibili_login import BiliLoginResult

    _clear_bili_login(env)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        bili_api,
        "qr_login_bilibili",
        lambda timeout_s=180.0: BiliLoginResult(sessdata="fresh-sd", uid="12345", logged_at=now),
    )

    resp = env.client.post("/api/bilibili/login")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["logged_in"] is True
    assert body["uid"] == "12345"
    assert "sessdata" not in body

    # GET 显示已登录（同库现读）
    got = env.client.get("/api/bilibili/login").json()
    assert got["logged_in"] is True
    assert got["uid"] == "12345"
    assert got["logged_at"] is not None


def test_post_login_timeout_returns_400(env: SimpleNamespace, monkeypatch) -> None:
    from app.api import bilibili as bili_api
    from app.service.bilibili_login import BiliLoginTimeout

    monkeypatch.setattr(
        bili_api,
        "qr_login_bilibili",
        lambda timeout_s=180.0: (_ for _ in ()).throw(BiliLoginTimeout("超时")),
    )
    resp = env.client.post("/api/bilibili/login")
    assert resp.status_code == 400, resp.text
    assert "超时" in resp.json()["detail"]


def test_post_login_window_closed_returns_400(env: SimpleNamespace, monkeypatch) -> None:
    from app.api import bilibili as bili_api
    from app.service.bilibili_login import BiliLoginCancelled

    monkeypatch.setattr(
        bili_api,
        "qr_login_bilibili",
        lambda timeout_s=180.0: (_ for _ in ()).throw(BiliLoginCancelled("窗口被关闭")),
    )
    resp = env.client.post("/api/bilibili/login")
    assert resp.status_code == 400, resp.text


def test_resolve_bilibili_sessdata_db_overrides_env(env: SimpleNamespace, monkeypatch) -> None:
    """DB 行优先；.env 的 BILIBILI_SESSDATA 只是回退（热生效：现读）。"""
    from app.service.llm.resolve import resolve_bilibili_sessdata

    _clear_bili_login(env)
    # .env 回退值（monkeypatch Settings 实例，不动真实 .env）
    monkeypatch.setattr(get_settings(), "bilibili_sessdata", "env-sd")

    async def _resolve() -> str:
        async with env.shared["factory"]() as session:
            return await resolve_bilibili_sessdata(session, 1)

    assert env.client.portal.call(_resolve) == "env-sd"  # DB 空 → 回退 .env

    # POST 扫码落库 → DB 值优先
    from app.api import bilibili as bili_api
    from app.service.bilibili_login import BiliLoginResult

    monkeypatch.setattr(
        bili_api,
        "qr_login_bilibili",
        lambda timeout_s=180.0: BiliLoginResult(
            sessdata="db-sd", uid="1", logged_at=datetime.now(UTC)
        ),
    )
    env.client.post("/api/bilibili/login")

    assert env.client.portal.call(_resolve) == "db-sd"  # DB 优先
