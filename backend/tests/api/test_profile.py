"""GET /api/profile 测试 —— v38 收缩后只剩「账号身份 + 是否已填章程」。

原四块聚合里的 settings / stats / charter.preview 已按主人 2026-11-25 铁律
「不重叠功能和内容」删除:它们都是别处端点的第二份拷贝,前端 UI 删掉后无人消费。

本文件现在守三件事:
① 响应形状就是 `{user, charter:{has_charter}}`,不多不少;
② has_charter 跟随 app_settings.charter 的真空/非空;
③ **密钥相关内容完全不出这个端点** —— 从根上消除「同一份掩码两处返回」。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from app.models import AppSettings


def _profile(env: SimpleNamespace) -> dict[str, Any]:
    return env.client.get("/api/profile").json()


def test_requires_token(env: SimpleNamespace) -> None:
    env.client.headers.pop("Authorization", None)
    assert env.client.get("/api/profile").status_code == 401


def test_response_shape_is_minimal(env: SimpleNamespace) -> None:
    """顶层只有 user 与 charter,且 charter 只有一个布尔。"""
    body = _profile(env)
    assert set(body.keys()) == {"user", "charter"}
    assert set(body["user"].keys()) == {"id", "username", "created_at"}
    assert set(body["charter"].keys()) == {"has_charter"}


@pytest.mark.parametrize("removed", ["settings", "stats"])
def test_removed_blocks_stay_removed(env: SimpleNamespace, removed: str) -> None:
    """v38 删掉的两块不许悄悄回来(请走 /api/settings 与各自计数)。"""
    body = _profile(env)
    assert removed not in body, f"{removed} 已删(同一信息只留一处)"


def test_charter_preview_stays_removed(env: SimpleNamespace) -> None:
    """charter.preview 已删 —— 完整章程走 GET /api/charter。"""
    assert "preview" not in _profile(env)["charter"]


def test_user_info(env: SimpleNamespace) -> None:
    body = _profile(env)
    assert body["user"]["id"] == 1
    assert body["user"]["username"]
    assert body["user"]["created_at"]


def test_has_charter_reflects_db(env: SimpleNamespace) -> None:
    """has_charter 跟随 app_settings.charter 的真空/非空。"""

    async def set_charter(text: str | None) -> None:
        async with env.shared["factory"]() as session:
            await session.execute(
                update(AppSettings).where(AppSettings.owner_id == 1).values(charter=text)
            )
            await session.commit()

    env.client.portal.call(lambda: set_charter("## 我的章程\n\n收技术笔记。"))
    assert _profile(env)["charter"]["has_charter"] is True

    env.client.portal.call(lambda: set_charter(None))
    assert _profile(env)["charter"]["has_charter"] is False


def test_no_key_material_anywhere(env: SimpleNamespace) -> None:
    """整个响应里不许出现密钥字段/末四位 —— v38 后密钥完全不出这个端点。"""
    env.client.put(
        "/api/settings",
        json={"llm_api_key": "sk-probe-secret-1234", "tavily_api_key": "tvly-probe-abcd"},
    )
    raw = env.client.get("/api/profile").text
    for token in ("api_key", "tavily", "sk-probe-secret", "1234", "abcd"):
        assert token not in raw, f"profile 响应出现了密钥相关内容:{token}"
