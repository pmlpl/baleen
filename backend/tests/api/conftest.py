"""上传 API 测试环境(线 I-批2)。

- 不起真服务器:TestClient 直接打 ASGI;用 with 块进入 → 单一 portal 事件循环,
  lifespan 会跑(只创建 redis client / engine 对象,不发起任何网络连接);
- DB:依赖覆盖 get_session → 外层事务 + savepoint,测试结束经 portal 回滚,
  不污染开发库(与 tests/service/import/conftest.py 同一模式);
- storage / 暂存目录指到 tmp_path;enqueue 换成 fake(不连真 Redis);
- PostgreSQL 不可用时整组 skip。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

# 必须在任何 get_settings() 调用之前:API 测试的 lifespan 不做嵌入模型预热
# (否则每次测试会话都真加载 bge,又慢又占显存;预热行为由真实启动人工验证)
os.environ["EMBED_WARMUP_ON_STARTUP"] = "false"

from app.api.deps import get_session
from app.core.config import get_settings
from app.service.imports import pipeline as pipeline_module


async def _ping(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def db_url() -> str:
    url = get_settings().database_url
    try:
        asyncio.run(_ping(url))
    except Exception as exc:  # noqa: BLE001 —— 环境缺失统一 skip,不报红
        pytest.skip(f"需要 PostgreSQL(docker compose up -d):{exc}")
    return url


@pytest.fixture
def env(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.api import upload as upload_module
    from app.main import app

    monkeypatch.setattr(pipeline_module, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr(upload_module, "STAGING_ROOT", tmp_path / "uploads")
    calls: list[dict[str, Any]] = []

    async def fake_enqueue(
        file_path: str, *, dedupe_key: str, no_llm: bool = False, owner_id: int = 1
    ) -> str | None:
        calls.append(
            {
                "file_path": file_path,
                "dedupe_key": dedupe_key,
                "no_llm": no_llm,
                "owner_id": owner_id,
            }
        )
        return dedupe_key

    monkeypatch.setattr(upload_module, "enqueue_import", fake_enqueue)

    # 外层事务在 portal 循环上急切建立;所有请求共享,结束一并回滚
    shared: dict[str, Any] = {}

    async def override() -> AsyncIterator[AsyncSession]:
        async with shared["factory"]() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        with TestClient(app) as client:

            async def _setup() -> None:
                shared["engine"] = create_async_engine(db_url)
                shared["conn"] = await shared["engine"].connect()
                # 先开事务,再插测试账号 —— 结束时整笔回滚,不污染开发库
                shared["trans"] = await shared["conn"].begin()
                # id=1 = 「库主人」:现有 API 测试造数据都写 owner_id=1,夹具账号对齐它
                await shared["conn"].execute(
                    text(
                        "INSERT INTO users (id, username, password_hash) "
                        "VALUES (1, 'test-user', 'x') ON CONFLICT (id) DO NOTHING"
                    )
                )
                shared["factory"] = async_sessionmaker(
                    bind=shared["conn"],
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )

            client.portal.call(_setup)

            # 所有测试请求默认带上该账号的 token(现有 API 测试无需逐个改)
            from app.service.auth.token import create_access_token

            shared["token"] = create_access_token(1, "test-user")
            client.headers["Authorization"] = f"Bearer {shared['token']}"
            shared["owner_id"] = 1

            try:
                yield SimpleNamespace(
                    client=client,
                    calls=calls,
                    shared=shared,
                    tmp_path=tmp_path,
                    token=shared["token"],
                    owner_id=1,
                )
            finally:

                async def _teardown() -> None:
                    await shared["trans"].rollback()
                    await shared["conn"].close()
                    await shared["engine"].dispose()

                client.portal.call(_teardown)
    finally:
        app.dependency_overrides.clear()
