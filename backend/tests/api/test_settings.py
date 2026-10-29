"""设置 API 测试:DB 单行优先/回退 .env、api_key 只写不读、事务隔离。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.config import get_settings


def test_get_falls_back_to_env(env: SimpleNamespace) -> None:
    """未在设置页改过 → 返回 .env 的生效值。"""
    resp = env.client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["llm_base_url"] == get_settings().llm_base_url
    assert body["llm_model"] == get_settings().llm_model
    assert body["api_key_set"] is True  # .env 配了 key
    assert len(body["api_key_tail"]) == 4


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
