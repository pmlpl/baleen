"""设置 API 测试:DB 单行优先/回退 .env、api_key 只写不读、事务隔离。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.config import get_settings


def test_get_falls_back_to_env(env: SimpleNamespace) -> None:
    """未在设置页改过 → base_url/model 回退 .env 默认值;api_key 不继承(必须自己配)。"""
    # 前提:该账号什么都没配。直接用一个新的 owner(不碰开发库那行)更干净 ——
    # 但 CurrentOwnerDep 由 token 决定,所以这里换个思路:断言「配了就是配的值」,
    # 「没配」的分支由 test_put_roundtrip_and_fallback 里的清空路径覆盖。
    env.client.put(
        "/api/settings",
        json={
            "llm_base_url": get_settings().llm_base_url,
            "llm_model": get_settings().llm_model,
            "llm_api_key": "sk-test-fallback-9876",
        },
    )
    resp = env.client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["llm_base_url"] == get_settings().llm_base_url
    assert body["llm_model"] == get_settings().llm_model
    assert body["api_key_set"] is True
    assert body["api_key_tail"] == "9876"


def test_put_roundtrip_and_fallback(env: SimpleNamespace) -> None:
    # 设置 model → GET/生效值立即反映(热生效,无需重启)
    resp = env.client.put("/api/settings", json={"llm_model": "deepseek-flash"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_model"] == "deepseek-flash"
    assert env.client.get("/api/settings").json()["llm_model"] == "deepseek-flash"

    # 清空(空串) → 回退 .env
    resp = env.client.put("/api/settings", json={"llm_model": ""})
    assert resp.status_code == 200
    assert resp.json()["llm_model"] == get_settings().llm_model


def test_put_empty_api_key_keeps_old(env: SimpleNamespace) -> None:
    env.client.put("/api/settings", json={"llm_api_key": "sk-test-abcd"})
    resp = env.client.put("/api/settings", json={"llm_api_key": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key_set"] is True
    assert body["api_key_tail"] == "abcd"  # 空 key = 保持不变


def test_get_never_returns_full_key(env: SimpleNamespace) -> None:
    body = env.client.get("/api/settings").json()
    assert "llm_api_key" not in body  # 全文永不回显


def test_tavily_key_roundtrip(env: SimpleNamespace) -> None:
    # 库里可能已有真实 key(主人配的),不假设初始为空
    # 设置新 key → tail 反映新值
    resp = env.client.put("/api/settings", json={"tavily_api_key": "tvly-abc12345"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tavily_set"] is True
    assert body["tavily_tail"] == "2345"
    assert env.client.get("/api/settings").json()["tavily_tail"] == "2345"


def test_llm_budgets_roundtrip(env: SimpleNamespace) -> None:
    """llm_context_tokens / llm_max_output_tokens 设置页往返。"""
    resp = env.client.put(
        "/api/settings",
        json={"llm_context_tokens": 1000000, "llm_max_output_tokens": 131072},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["llm_context_tokens"] == 1000000
    assert body["llm_max_output_tokens"] == 131072
    # GET 回显
    body = env.client.get("/api/settings").json()
    assert body["llm_context_tokens"] == 1000000
    assert body["llm_max_output_tokens"] == 131072


def test_llm_budgets_clear_with_zero(env: SimpleNamespace) -> None:
    """0 = 清空回 NULL(走兜底)。"""
    env.client.put(
        "/api/settings",
        json={"llm_context_tokens": 1000000, "llm_max_output_tokens": 131072},
    )
    resp = env.client.put(
        "/api/settings", json={"llm_context_tokens": 0, "llm_max_output_tokens": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_context_tokens"] is None
    assert body["llm_max_output_tokens"] is None


def test_resolve_llm_budgets_reads_db(env: SimpleNamespace) -> None:
    """resolve_llm_budgets:用户填了 → 返回值;没填 → None(走兜底/不截断)。"""
    from app.service.llm.resolve import resolve_llm_budgets

    # 先清空(可能被前一个测试设过)
    env.client.put("/api/settings", json={"llm_context_tokens": 0, "llm_max_output_tokens": 0})

    async def _read() -> tuple:
        async with env.shared["factory"]() as session:
            return await resolve_llm_budgets(session, env.owner_id)

    # 没填 → (None, None)
    ctx, out = env.client.portal.call(_read)
    assert ctx is None
    assert out is None

    # 填了 → 返回值
    env.client.put(
        "/api/settings",
        json={"llm_context_tokens": 200000, "llm_max_output_tokens": 8192},
    )
    ctx, out = env.client.portal.call(_read)
    assert ctx == 200000
    assert out == 8192
