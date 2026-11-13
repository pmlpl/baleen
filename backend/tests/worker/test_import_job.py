"""arq worker 任务编排测试(线 I-批2)。

- run_import_async 换成 fake(monkeypatch app.service.imports 包属性 —— import_file 是
  函数级延迟导入,每次调用从包属性取),不真跑解析、不加载 bge;
- 需要 DB 的用例(打 g2_skipped 标记)用外层事务 + savepoint 隔离,PostgreSQL 不可用则 skip;
- 不连真 Redis(enqueue_import 不在单测范围,冒烟里真跑)。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.repo import Repos
from app.service.imports.pipeline import ImportResult
from app.worker import worker as worker_module
from app.worker.worker import WorkerSettings, clean_stale_uploads, import_file

PortalFn = Callable[..., Awaitable[Any]]


def test_worker_settings_registers_import_task() -> None:
    assert import_file in WorkerSettings.functions
    assert WorkerSettings.max_jobs == 1
    assert WorkerSettings.job_timeout == 900
    assert WorkerSettings.redis_settings.host  # 来自 config.redis_url,非默认 127.0.0.1 也能构造


# ---------- 暂存区清理 ----------


def test_clean_stale_uploads_removes_only_old_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
    old = tmp_path / "old_a.md"
    fresh = tmp_path / "fresh_b.md"
    old.write_bytes(b"a")
    fresh.write_bytes(b"b")
    past = time.time() - 25 * 3600
    os.utime(old, (past, past))

    removed = clean_stale_uploads(max_age_hours=24)

    assert removed == [old.name]
    assert not old.exists()
    assert fresh.exists()


def test_clean_stale_uploads_missing_dir_is_noop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path / "nope")
    assert clean_stale_uploads() == []


def test_remove_staging_refuses_paths_outside_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path / "uploads")
    outside = tmp_path / "elsewhere.md"
    outside.write_bytes(b"x")
    assert worker_module._remove_staging(outside) is False
    assert outside.exists()  # 任务参数不可信,根目录之外一律不删


# ---------- import_file 编排(fake pipeline,不需要 DB 的部分)----------


class _StubSession:
    """最小假 session:scalar → None,resolve_charter 回退内置默认章程。

    import_file 现在读 charter(需 session),这两个编排测试不连真 DB,
    用 stub 避免跨 asyncio.run 事件循环复用 asyncpg 连接。
    """

    async def scalar(self, *_a, **_kw):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def _stub_factory():
    return _StubSession()


def test_import_file_success_removes_staging(tmp_path: Path, monkeypatch) -> None:
    import app.service.imports as imports_pkg

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(worker_module, "session_factory", _stub_factory)
    staging = tmp_path / "1_a.md"
    staging.write_bytes(b"x")
    seen: dict[str, Any] = {}

    async def fake_run(path: str, **kwargs: Any) -> ImportResult:
        seen["path"] = path
        seen["kwargs"] = kwargs
        return ImportResult(source_id=7, note_id=3, parse_status="ok", action="index")

    monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)
    result = asyncio.run(import_file({}, str(staging)))

    assert seen["kwargs"]["worker"] == worker_module.WORKER_NAME
    assert seen["kwargs"]["no_llm"] is False
    assert result["source_id"] == 7
    assert result["note_id"] == 3
    assert not staging.exists()  # 成功后删暂存


def test_import_file_failure_keeps_staging_for_retry(tmp_path: Path, monkeypatch) -> None:
    import app.service.imports as imports_pkg

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(worker_module, "session_factory", _stub_factory)
    staging = tmp_path / "2_a.md"
    staging.write_bytes(b"x")

    async def fake_run(path: str, **kwargs: Any) -> ImportResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(import_file({}, str(staging)))
    assert staging.exists()  # 失败保留,arq 重试 / 24h 清理兜底


def test_remove_staging_deletes_file_inside_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path / "uploads")
    inside = tmp_path / "uploads" / "3_a.md"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"x")
    assert worker_module._remove_staging(str(inside)) is True
    assert not inside.exists()


# ---------- g2_skipped 标记(需要 DB)----------


@pytest.fixture
def db_scope(db_url: str):
    """每个用例独立的外层事务,结束回滚,不污染开发库。"""

    async def scenario(fn: PortalFn) -> Any:
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


def test_import_file_marks_g2_skipped(db_scope, tmp_path: Path, monkeypatch) -> None:
    import app.service.imports as imports_pkg

    async def scenario(factory: async_sessionmaker) -> None:
        async with factory() as session:
            source, _ = await Repos(session, 1).sources.create_or_get_by_sha256(
                sha256=hashlib.sha256(os.urandom(8)).hexdigest(),
                filename="seed.md",
                ext=".md",
                size_bytes=10,
                storage_path="unused",
                parse_status="error",
            )
            await session.commit()
            source_id = source.id

        staging = tmp_path / "4_a.md"
        staging.write_bytes(b"x")
        monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
        monkeypatch.setattr(worker_module, "session_factory", factory)

        async def fake_run(path: str, **kwargs: Any) -> ImportResult:
            return ImportResult(source_id=source_id, parse_status="ok", action="index")

        monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)
        result = await import_file({}, str(staging), no_llm=True)

        assert result["source_id"] == source_id
        assert not staging.exists()
        async with factory() as session:
            src = await Repos(session, 1).sources.get(source_id)
            assert src is not None
            assert src.parse_meta["g2_skipped"] is True
            assert src.parse_status == "error"  # 其余字段原样保留

    asyncio.run(db_scope(scenario))


def test_import_file_no_marker_on_dedupe_or_llm_mode(db_scope, tmp_path: Path, monkeypatch) -> None:
    import app.service.imports as imports_pkg

    async def scenario(factory: async_sessionmaker) -> None:
        async with factory() as session:
            source, _ = await Repos(session, 1).sources.create_or_get_by_sha256(
                sha256=hashlib.sha256(os.urandom(8)).hexdigest(),
                filename="seed2.md",
                ext=".md",
                size_bytes=10,
                storage_path="unused",
                parse_status="ok",
            )
            await session.commit()
            source_id = source.id

        staging = tmp_path / "5_a.md"
        staging.write_bytes(b"x")
        monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
        monkeypatch.setattr(worker_module, "session_factory", factory)

        async def fake_run(path: str, **kwargs: Any) -> ImportResult:
            return ImportResult(source_id=source_id, deduped=True, parse_status="ok")

        monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)
        # deduped=True:真跑过(或复用)的 source,不冤记 g2_skipped
        await import_file({}, str(staging), no_llm=True)
        # no_llm=False:正常模式与标记无关
        await import_file({}, str(staging), no_llm=False)

        async with factory() as session:
            src = await Repos(session, 1).sources.get(source_id)
            assert src is not None
            assert "g2_skipped" not in src.parse_meta
        assert not staging.exists()

    asyncio.run(db_scope(scenario))
