"""导入流水线测试的隔离环境。

- PostgreSQL 必须可用(docker compose up -d);不可用则整组 skip。
- 每个用例跑在**外层事务**里,结束时 rollback → 不污染开发库。
- blob 写入重定向到 tmp_path,不碰真实 baleen/storage。
- embedder / token 计数用假实现(测试不加载 1.2 GB 模型)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
def db_scope(db_url: str) -> Callable[[], AbstractAsyncContextManager[async_sessionmaker]]:
    """返回一个「外层事务 + savepoint」的 session 工厂上下文管理器。"""

    @asynccontextmanager
    async def scope():
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                trans = await conn.begin()
                factory = async_sessionmaker(
                    bind=conn,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                try:
                    yield factory
                finally:
                    await trans.rollback()
        finally:
            await engine.dispose()

    return scope


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "STORAGE_ROOT", tmp_path / "storage")


@pytest.fixture
def fake_embed() -> Callable[[list[str]], list[list[float]]]:
    return lambda texts: [[0.5] * 1024 for _ in texts]


@pytest.fixture
def fake_tokens() -> Callable[[str], int]:
    return lambda text: max(1, len(text))


def run_kwargs(fake_embed, fake_tokens) -> dict:
    return {"embed_func": fake_embed, "token_counter": fake_tokens}
