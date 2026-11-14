"""extract_and_import_video worker 任务测试。

- extract_video_content 换成 fake（不联网、不加载 whisper）；
- run_import_async 换成 fake（不真跑解析、不加载 bge）；
- 需要 DB 的用例（进度更新、job 失败标记）用外层事务 + savepoint 隔离。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.repo import Repos
from app.service.imports.pipeline import ImportResult
from app.worker import worker as worker_module
from app.worker.worker import (
    WorkerSettings,
    extract_and_import_video,
    import_file,
)

PortalFn = Any  # callable returning Awaitable[Any]


# ---------- WorkerSettings ----------


def test_worker_settings_registers_video_task() -> None:
    # 视频任务用 arq func() 包装注册（timeout=7200），functions 里是 Function 对象
    names = [getattr(f, "name", None) or f.__name__ for f in WorkerSettings.functions]
    assert "import_file" in names
    assert "extract_and_import_video" in names
    video_fn = next(f for f in WorkerSettings.functions if getattr(f, "name", "") == "extract_and_import_video")
    assert video_fn.timeout_s == 7200  # 2 小时（enqueue_job 没有 _timeout 参数，只能在这里设）
    assert WorkerSettings.job_timeout == 900  # 文件导入保持 15 分钟


# ---------- stub session（不需要 DB 的编排测试）----------


class _StubSession:
    """最小假 session:scalar → None，resolve_charter/resolve_llm_config 回退默认。"""

    async def scalar(self, *_a, **_kw):
        return None

    async def execute(self, *_a, **_kw):
        pass

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def _stub_factory():
    return _StubSession()


# ---------- 成功路径 ----------


def test_extract_video_success_calls_pipeline_with_source_id(
    tmp_path: Path, monkeypatch
) -> None:
    """提取成功 → run_import_async 被调用且 source_id 传入；暂存被清理。"""
    import app.service.imports as imports_pkg
    import app.service.video_extractor as vextract

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(worker_module, "session_factory", _stub_factory)

    # fake extraction
    fake_vc = type("VC", (), {"markdown": "# 测试视频\n\n内容", "title": "测试视频"})()

    def fake_extract(url, *, sessdata="", cookies_str="", client=None):
        return fake_vc

    monkeypatch.setattr(vextract, "extract_video_content", fake_extract)

    # fake pipeline
    seen: dict[str, Any] = {}

    async def fake_run(path: str, **kwargs: Any) -> ImportResult:
        seen["path"] = path
        seen["kwargs"] = kwargs
        return ImportResult(source_id=42, note_id=5, parse_status="ok", action="index")

    monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)

    result = asyncio.run(
        extract_and_import_video(
            {}, "https://www.bilibili.com/video/BV1GJ411x7h7/",
            owner_id=1, source_id=42, mode="whisper",
        )
    )

    assert result["source_id"] == 42
    assert seen["kwargs"]["source_id"] == 42
    assert seen["kwargs"]["worker"] == worker_module.WORKER_NAME
    # 暂存文件已清理
    staging_files = list(tmp_path.rglob("*.md"))
    assert staging_files == []


def test_extract_video_writes_markdown_to_staging(tmp_path: Path, monkeypatch) -> None:
    """提取后 markdown 写入暂存区，文件名是视频标题净化后。"""
    import app.service.imports as imports_pkg
    import app.service.video_extractor as vextract

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(worker_module, "session_factory", _stub_factory)

    fake_vc = type("VC", (), {"markdown": "# 测试\n\n内容", "title": "测试"})()
    monkeypatch.setattr(vextract, "extract_video_content", lambda *a, **kw: fake_vc)

    staging_info: list[dict[str, Any]] = []

    async def fake_run(path: str, **kwargs: Any) -> ImportResult:
        p = Path(path)
        staging_info.append({"path": path, "name": p.name, "exists": p.exists(), "text": p.read_text(encoding="utf-8")})
        return ImportResult(source_id=1, parse_status="ok")

    monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)

    asyncio.run(
        extract_and_import_video(
            {}, "https://www.bilibili.com/video/BV1GJ411x7h7/",
            owner_id=1, source_id=1, mode="whisper",
        )
    )

    # 在 fake_run 内部检查（worker 成功后会删暂存）
    assert len(staging_info) == 1
    info = staging_info[0]
    assert info["exists"] is True
    assert info["name"].endswith(".md")
    assert "测试" in info["name"]
    assert "测试" in info["text"]


# ---------- 提取失败 ----------


def test_extract_video_failure_marks_job_failed(
    db_scope, tmp_path: Path, monkeypatch
) -> None:
    """提取失败 → job 标记 failed + source.parse_meta 显示错误。"""
    import app.service.imports as imports_pkg
    import app.service.video_extractor as vextract

    async def scenario(factory: async_sessionmaker) -> None:
        # 预创建 stub source + job（模拟 API 的行为）
        async with factory() as session:
            repos = Repos(session, 1)
            source, _ = await repos.sources.create_or_get_by_sha256(
                sha256="video:BVtest:whisper",
                filename="BVtest.md",
                ext=".md",
                size_bytes=0,
                storage_path="",
                mime="text/markdown",
                parse_status="pending",
            )
            meta = {"video_mode": "whisper", "video_stage": "排队中"}
            await repos.sources.update_parse_meta(source.id, meta)
            job, _ = await repos.jobs.create_or_get(
                dedupe_key="import:video:BVtest:whisper",
                payload={"url": "test", "mode": "whisper"},
                kind="import",
            )
            await session.commit()
            source_id = source.id
            job_id = job.id

        monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
        monkeypatch.setattr(worker_module, "session_factory", factory)

        # fake extraction raises
        def boom(url, *, sessdata="", cookies_str="", client=None):
            raise RuntimeError("download failed")

        monkeypatch.setattr(vextract, "extract_video_content", boom)

        # fake pipeline（不应被调）
        async def fake_run(path: str, **kwargs: Any) -> ImportResult:
            raise AssertionError("pipeline should not be called on extraction failure")

        monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)

        with pytest.raises(RuntimeError, match="download failed"):
            await extract_and_import_video(
                {}, "https://www.bilibili.com/video/BVtest/",
                owner_id=1, source_id=source_id, mode="whisper",
            )

        # 验证 job 标记 failed
        async with factory() as session:
            repos = Repos(session, 1)
            src = await repos.sources.get(source_id)
            assert src is not None
            assert "video_stage" in src.parse_meta
            assert "❌" in src.parse_meta["video_stage"]
            job = await repos.jobs.get(job_id)
            assert job is not None
            assert job.status == "failed"
            assert "download failed" in (job.last_error or "")

    asyncio.run(db_scope(scenario))


# ---------- subtitle 模式（worker 也支持）----------


def test_extract_video_subtitle_mode(tmp_path: Path, monkeypatch) -> None:
    """subtitle 模式：worker 调 fetch_bilibili 而非 extract_video_content。"""
    import app.service.imports as imports_pkg
    import app.service.bilibili as bili

    monkeypatch.setattr(worker_module, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(worker_module, "session_factory", _stub_factory)

    fake_result = bili.BilibiliResult(
        info=bili.BilibiliVideoInfo(
            bvid="BV1test", aid=1, cid=2, title="测试",
            description="", owner_name="UP", duration=60, chapters=[],
        ),
        markdown="# 测试\n\n字幕内容",
    )
    monkeypatch.setattr(bili, "fetch_bilibili", lambda url, *, sessdata="": fake_result)

    async def fake_run(path: str, **kwargs: Any) -> ImportResult:
        return ImportResult(source_id=1, parse_status="ok")

    monkeypatch.setattr(imports_pkg, "run_import_async", fake_run)

    result = asyncio.run(
        extract_and_import_video(
            {}, "https://www.bilibili.com/video/BV1test/",
            owner_id=1, source_id=1, mode="subtitle",
        )
    )
    assert result["source_id"] == 1


# ---------- cookie 字符串解析 ----------


def test_write_cookie_string_parses_pairs(tmp_path: Path) -> None:
    from app.service.video_extractor import _resolve_cookie_file

    cf = _resolve_cookie_file(cookies_str="ttwid=abc; msToken=xyz", output_dir=tmp_path)
    assert cf is not None
    text = Path(cf).read_text(encoding="utf-8")
    assert "ttwid\tabc" in text
    assert "msToken" in text


def test_write_cookie_string_bare_value_becomes_ttwid(tmp_path: Path) -> None:
    """用户只粘贴 ttwid 的纯值（无 = 号）→ 默认当作 ttwid。"""
    from app.service.video_extractor import _resolve_cookie_file

    raw = "1%7C_MDIALLS-xxx%7C1794615809%7C9b679e"
    cf = _resolve_cookie_file(cookies_str=raw, output_dir=tmp_path)
    assert cf is not None
    text = Path(cf).read_text(encoding="utf-8")
    assert "ttwid" in text
    assert raw in text


def test_write_cookie_string_strips_cookie_prefix(tmp_path: Path) -> None:
    """粘贴整个请求头（带 Cookie: 前缀）也能解析。"""
    from app.service.video_extractor import _resolve_cookie_file

    cf = _resolve_cookie_file(cookies_str="Cookie: ttwid=abc; msToken=xyz", output_dir=tmp_path)
    assert cf is not None
    text = Path(cf).read_text(encoding="utf-8")
    assert "ttwid" in text and "msToken" in text
    assert "Cookie:" not in text.split("# Netscape HTTP Cookie File")[1]


# ---------- DB fixture（同 test_import_job.py 模式）----------


@pytest.fixture
def db_scope(db_url: str):
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
