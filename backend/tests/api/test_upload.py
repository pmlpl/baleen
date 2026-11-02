"""上传 API 测试(线 I-批2):TestClient + 临时 storage + fake enqueue。

不连真 Redis、不加载 bge 模型;DB 走外层事务隔离(conftest 的 api_env)。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.repo import Repos

CONTENT = "# hello\n\n这是一段足够真实的上传内容,用于验证导入链路。\n".encode()

PortalFn = Callable[..., Awaitable[Any]]


def _upload(env, name: str = "hello.md", content: bytes = CONTENT, no_llm: bool = False):
    return env.client.post(
        "/api/sources",
        files={"file": (name, content, "application/octet-stream")},
        params={"no_llm": no_llm},
    )


def _portal_call(env, fn: PortalFn, *args: Any) -> Any:
    """在 TestClient 的 portal 循环上跑一段异步代码(和请求同一个事件循环)。"""
    return env.client.portal.call(fn, *args)


def test_upload_new_file_creates_source_job_and_blob(env) -> None:
    resp = _upload(env)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduped"] is False
    assert body["job_status"] == "queued"

    assert len(env.calls) == 1
    call = env.calls[0]
    assert call["no_llm"] is False
    sha = call["dedupe_key"].removeprefix("import:")
    assert len(sha) == 64

    # 暂存文件在,扩展名保留(G0 按扩展名路由)
    staging = Path(call["file_path"])
    assert staging.exists() and staging.suffix == ".md"
    # blob 已按内容寻址落盘(storage/{sha[:2]}/{sha},AGENTS §4)
    assert (env.tmp_path / "storage" / sha[:2] / sha).exists()

    async def check() -> None:
        async with env.shared["factory"]() as session:
            repos = Repos(session, 1)
            source = await repos.sources.get(body["source_id"])
            assert source is not None
            assert source.sha256 == sha
            assert source.filename == "hello.md"
            assert source.parse_status == "pending"  # 等 worker 跑 G0;解析完由流水线改真值
            job = await repos.jobs.get_by_dedupe_key("import:" + sha)
            assert job is not None
            assert job.status == "queued"
            assert job.payload["filename"] == "hello.md"
            assert job.payload["no_llm"] is False

    _portal_call(env, check)


def test_upload_dedupe_hit_returns_same_source_and_skips_new_job(env) -> None:
    first = _upload(env)
    second = _upload(env)
    assert first.status_code == 201
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["deduped"] is True
    assert body["source_id"] == first.json()["source_id"]
    assert body["job_id"] == first.json()["job_id"]

    # 未完成任务重复入队是允许的,由 arq _job_id 幂等挡掉;dedupe_key 一致
    assert len(env.calls) == 2
    assert env.calls[1]["dedupe_key"] == env.calls[0]["dedupe_key"]

    # 任务在途:两次的暂存文件都保留(第二次的可能被自愈重入队用到;
    # 若被 arq 幂等挡掉则成为孤儿,由 worker 启动时的 24h 清理兜底)
    assert Path(env.calls[0]["file_path"]).exists()
    assert Path(env.calls[1]["file_path"]).exists()


def test_upload_unknown_extension_rejected_415(env) -> None:
    resp = _upload(env, name="evil.exe", content=b"MZfake")
    assert resp.status_code == 415
    assert "暂不支持的格式" in resp.json()["detail"]
    assert env.calls == []
    uploads = env.tmp_path / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []  # 暂存没落任何东西


def test_upload_known_unsupported_and_image_exts_accepted(env) -> None:
    """白名单含 G0 认识的「不支持格式」与图片:收下,交给 G0 给具体提示/状态。"""
    doc = _upload(env, name="legacy.doc", content=b"old binary doc")
    png = _upload(env, name="photo.png", content=b"\x89PNG fake")
    assert doc.status_code == 201
    assert png.status_code == 201


def test_upload_oversize_stream_limit_413(env, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import upload as upload_module

    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 64)
    resp = _upload(env, name="big.md", content=b"x" * 128)
    assert resp.status_code == 413
    assert "大小上限" in resp.json()["detail"]
    assert env.calls == []  # 没入队
    assert list((env.tmp_path / "uploads").iterdir()) == []  # 暂存已清理
    assert not (env.tmp_path / "storage").exists()  # blob 没写


def test_upload_body_limit_by_content_length_header(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Content-Length 头先挡:在解析 multipart 之前拒绝(不白收大文件)。"""
    from app.api import upload as upload_module

    monkeypatch.setattr(upload_module, "BODY_LIMIT_BYTES", 8)
    resp = _upload(env, name="small.md", content=b"1234567890")
    assert resp.status_code == 413
    assert "请求体超过上限" in resp.json()["detail"]
    assert env.calls == []


def test_upload_missing_filename_422(env) -> None:
    resp = env.client.post(
        "/api/sources",
        files={"file": ("", CONTENT, "application/octet-stream")},
    )
    assert resp.status_code == 422


def test_get_source_status_after_upload(env) -> None:
    source_id = _upload(env).json()["source_id"]
    resp = env.client.get(f"/api/sources/{source_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == source_id
    assert body["filename"] == "hello.md"
    assert body["ext"] == ".md"
    assert body["parse_status"] == "pending"  # 等 G0;任务跑完才是真值
    assert body["job"]["status"] == "queued"
    assert body["job"]["attempts"] == 0
    assert body["review_runs"] == []
    assert body["note_id"] is None
    assert body["g2_skipped"] is False


def test_get_source_not_found(env) -> None:
    resp = env.client.get("/api/sources/999999")
    assert resp.status_code == 404


def test_get_source_with_runs_note_and_g2_skipped(env) -> None:
    """预插一条带 G1 判定/note/g2_skipped 标记的 source,验证 GET 的聚合输出。"""

    async def seed() -> tuple[int, int]:
        sha = hashlib.sha256(os.urandom(8)).hexdigest()
        async with env.shared["factory"]() as session:
            repos = Repos(session, 1)
            source, _ = await repos.sources.create_or_get_by_sha256(
                sha256=sha,
                filename="seeded.md",
                ext=".md",
                size_bytes=42,
                storage_path="unused",
                parse_status="ok",
            )
            await repos.sources.update_parse(
                source.id,
                parse_status="ok",
                parse_meta={"chars": 42, "warnings": ["w1"], "g2_skipped": True},
                doc={"type": "doc", "content": []},
            )
            await repos.review_runs.add(
                source_id=source.id,
                gate="G1",
                verdict="accept",
                quality="core",
                score=1.0,
                action="index",
                reasons=["r1"],
            )
            note = await repos.notes.create(
                source_id=source.id,
                title="t",
                doc={"type": "doc", "content": []},
                quality="core",
            )
            await session.commit()
            return source.id, note.id

    source_id, note_id = _portal_call(env, seed)
    resp = env.client.get(f"/api/sources/{source_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parse_status"] == "ok"
    assert body["warnings"] == ["w1"]
    assert body["g2_skipped"] is True
    assert body["note_id"] == note_id
    assert [run["gate"] for run in body["review_runs"]] == ["G1"]
    run = body["review_runs"][0]
    assert run["verdict"] == "accept"
    assert run["quality"] == "core"
    assert run["score"] == 1.0
    assert run["action"] == "index"
    assert run["reasons"] == ["r1"]


# ---------------------------------------------------------------- md 附件(v16)

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


def test_upload_md_with_attachments_lands_in_same_staging_dir(env) -> None:
    """附件与主文件落在同一个暂存目录里,且保持相对路径(md 解析才能找到图)。"""
    md = "正文。\n\n![架构图](img/a.png)\n".encode()
    resp = env.client.post(
        "/api/sources",
        files={
            "file": ("note.md", md, "text/markdown"),
            "attachments": ("img/a.png", PNG_1X1, "image/png"),
        },
        params={"no_llm": False},
    )
    assert resp.status_code == 201, resp.text
    # enqueue 收到的是主文件路径;它和附件必须在同一目录里
    file_path = Path(env.calls[-1]["file_path"])
    assert file_path.name == "note.md"
    attached = file_path.parent / "img" / "a.png"
    assert attached.is_file(), f"附件应落在暂存目录的相对路径上:{attached}"
    assert attached.read_bytes() == PNG_1X1
    # md 解析器按 md 所在目录解析相对路径 → 能读到同一张图
    from app.service.parse.md_parser import parse_md

    stored: list[bytes] = []

    def sink(data: bytes, _alt: str) -> str:
        stored.append(data)
        return "0" * 64

    result = parse_md(file_path, media_sink=sink)
    assert stored == [PNG_1X1], "相对路径解析到同一张附件"
    assert [b for b in result.blocks["content"] if b["type"] == "image"]


def test_upload_rejects_non_image_attachment(env) -> None:
    resp = env.client.post(
        "/api/sources",
        files={
            "file": ("note.md", b"# hi\n", "text/markdown"),
            "attachments": ("evil.exe", b"MZ", "application/octet-stream"),
        },
        params={"no_llm": False},
    )
    assert resp.status_code == 415
    assert "图片" in resp.json()["detail"]


def test_upload_attachment_path_traversal_blocked(env) -> None:
    """文件名带 ../ 试图写出暂存目录 → 层级被剥掉,文件仍落在目录内。"""
    resp = env.client.post(
        "/api/sources",
        files={
            "file": ("note.md", b"# hi\n", "text/markdown"),
            "attachments": ("../../escape.png", PNG_1X1, "image/png"),
        },
        params={"no_llm": False},
    )
    assert resp.status_code == 201, resp.text
    staging_dir = Path(env.calls[-1]["file_path"]).parent
    assert (staging_dir / "escape.png").is_file(), "剥离 .. 后落在暂存目录内"
    assert not (staging_dir.parent.parent / "escape.png").exists(), "不许穿出暂存根"
