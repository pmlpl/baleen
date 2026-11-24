"""CORS preflight 端点测试(B5,2026-11-24):methods/headers 收紧为显式清单。

- 依据实测:后端路由只用 GET/POST/PUT/DELETE;前端只发 Authorization/Content-Type/Accept。
- preflight(OPTIONS + Origin + Access-Control-Request-Method)由 CORSMiddleware 直接应答,
  不走路由、不碰 DB/Redis → 用裸 TestClient,不依赖 docker。
- starlette 的失败语义:origin/method/header 任一不允许 → 400 "Disallowed CORS ..."。
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _whitelisted_origin() -> str:
    origins = get_settings().cors_origin_list
    assert origins, "CORS_ORIGINS 白名单不能为空(否则前端全挂,配置错误)"
    return origins[0]


def test_preflight_whitelisted_origin_with_allowed_method_and_headers() -> None:
    """白名单 Origin + 允许的 method/header → 放行,并回显允许清单。"""
    origin = _whitelisted_origin()
    resp = _client().options(
        "/api/qa/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type, accept",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == origin
    assert resp.headers["access-control-allow-credentials"] == "true"
    methods = resp.headers["access-control-allow-methods"]
    for m in ("GET", "POST", "PUT", "DELETE"):
        assert m in methods, f"收紧后的 methods 清单必须覆盖路由实际用到的 {m}:{methods}"
    headers = resp.headers["access-control-allow-headers"]
    assert "authorization" in headers.lower(), "前端带 token 必须能过 preflight"


def test_preflight_disallowed_method_rejected() -> None:
    """白名单 Origin 但 PATCH(无路由使用、未列入清单)→ 不放行。"""
    resp = _client().options(
        "/api/qa/chat",
        headers={
            "Origin": _whitelisted_origin(),
            "Access-Control-Request-Method": "PATCH",
        },
    )
    assert resp.status_code == 400
    assert "method" in resp.text


def test_preflight_non_whitelisted_origin_rejected() -> None:
    """非白名单 Origin → 不放行(收紧后依旧,origins 白名单逻辑不变)。"""
    resp = _client().options(
        "/api/qa/chat",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 400
    assert "origin" in resp.text


def test_preflight_disallowed_header_rejected() -> None:
    """允许的 Origin/method 但未列入清单的自定义头(x-baleen-*)→ 不放行。"""
    resp = _client().options(
        "/api/qa/chat",
        headers={
            "Origin": _whitelisted_origin(),
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-baleen-custom",
        },
    )
    assert resp.status_code == 400
    assert "headers" in resp.text
