"""检索测试环境:外层事务 + savepoint 隔离;PostgreSQL 不可用整组 skip。

fake embedder 注入固定向量,不加载 bge 模型。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings


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
def db_scope(db_url: str):
    """每个用例独立的外层事务,结束回滚,不污染开发库。"""

    async def scenario(fn) -> object:
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
                    return await fn(factory)
                finally:
                    await trans.rollback()
        finally:
            await engine.dispose()

    return scenario
