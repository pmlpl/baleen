"""worker 测试的 DB 探活:PostgreSQL 不可用时整组 skip(与 tests/api/conftest.py 同款)。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

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
