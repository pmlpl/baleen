"""POST /api/sources/url 测试:B 站链接导入。

用 TestClient(in-process)+ fake enqueue(不连真 Redis);
fetch_bilibili 通过 monkeypatch 替换成假数据,不联网。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.service import bilibili as bilibili_module


def _fake_bilibili_result() -> bilibili_module.BilibiliResult:
    return bilibili_module.BilibiliResult(
        info=bilibili_module.BilibiliVideoInfo(
            bvid="BV1GJ411x7h7",
            aid=12345,
            cid=67890,
            title="深入理解闭包",
            description="JS 闭包详解",
            owner_name="技术UP主",
            duration=1200,
            chapters=[],
        ),
        markdown="# 深入理解闭包\n\n> UP主: 技术UP主\n\n大家好。今天讲闭包。\n",
    )


def _post_url(env, url: str = "https://www.bilibili.com/video/BV1GJ411x7h7/", mode: str = "subtitle"):
    return env.client.post(
        "/api/sources/url",
        json={"url": url, "mode": mode},
    )


def test_url_import_creates_source_and_job(env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bilibili_module, "fetch_bilibili", lambda url, *, sessdata="": _fake_bilibili_result())

    resp = _post_url(env)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduped"] is False
    assert body["job_status"] == "queued"
    assert body["source_id"] > 0
    assert body["job_id"] > 0

    # enqueue 被调了一次,文件是 .md
    assert len(env.calls) == 1
    call = env.calls[0]
    assert call["owner_id"] == 1
    staging = Path(call["file_path"])
    assert staging.exists()
    assert staging.suffix == ".md"
    assert staging.name == "深入理解闭包.md"
    # markdown 内容写进了暂存
    assert "深入理解闭包" in staging.read_text(encoding="utf-8")

    # via=url 标记在 job payload 里
    sha = call["dedupe_key"].removeprefix("import:")

    async def check() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(body["source_id"])
            assert source is not None
            assert source.sha256 == sha
            assert source.filename == "深入理解闭包.md"
            assert source.ext == ".md"
            assert source.parse_status == "pending"
            job = await repos.jobs.get_by_dedupe_key("import:" + sha)
            assert job is not None
            assert job.payload["via"] == "url"

    env.client.portal.call(check)


def test_url_import_dedupe(env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bilibili_module, "fetch_bilibili", lambda url, *, sessdata="": _fake_bilibili_result())

    first = _post_url(env)
    assert first.status_code == 201
    second = _post_url(env)
    assert second.status_code == 200
    body = second.json()
    # v24 语义修正:job 还在 queued(未真完成过)→ deduped=False,前端继续轮询
    assert body["deduped"] is False
    assert body["source_id"] == first.json()["source_id"]


def test_url_import_done_job_dedupes(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """job 真完成过(done)→ 第二次导入 deduped=True,不再入队。"""
    monkeypatch.setattr(bilibili_module, "fetch_bilibili", lambda url, *, sessdata="": _fake_bilibili_result())

    first = _post_url(env)
    assert first.status_code == 201
    source_id = first.json()["source_id"]

    async def mark_done() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            await repos.jobs.update_status(job.id, "done")
            await session.commit()

    env.client.portal.call(mark_done)

    second = _post_url(env)
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is True
    assert body["source_id"] == source_id
    # done → 不再入队
    assert len(env.calls) == 1


def test_url_import_failed_job_retries(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """job 失败过 → 重新导入 = 重试:deduped=False,job 重置回 queued,重新入队。"""
    monkeypatch.setattr(bilibili_module, "fetch_bilibili", lambda url, *, sessdata="": _fake_bilibili_result())

    first = _post_url(env)
    assert first.status_code == 201
    source_id = first.json()["source_id"]

    async def mark_failed() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            await repos.jobs.update_status(job.id, "failed", last_error="boom")
            await session.commit()

    env.client.portal.call(mark_failed)

    second = _post_url(env)
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is False  # 重试不算去重,前端要继续轮询
    assert body["source_id"] == source_id
    assert len(env.calls) == 2  # 重新入队了

    async def check_reset() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            assert job.status == "queued"  # 已重置
            assert job.last_error is None  # 旧错误已清

    env.client.portal.call(check_reset)


def test_url_import_invalid_url(env) -> None:
    resp = _post_url(env, url="https://www.youtube.com/watch?v=abc")
    assert resp.status_code == 400
    assert "BV" in resp.json()["detail"] or "链接" in resp.json()["detail"]


def test_url_import_no_subtitle(env, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_no_sub(url: str, *, sessdata: str = "") -> Any:
        raise bilibili_module.BilibiliError("该视频没有字幕(CC 字幕或 AI 字幕),暂无法导入。")

    monkeypatch.setattr(bilibili_module, "fetch_bilibili", raise_no_sub)
    resp = _post_url(env)
    assert resp.status_code == 400
    assert "没有字幕" in resp.json()["detail"]
    assert env.calls == []  # 没入队


def test_url_import_bilibili_api_error(env, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_network(url: str, *, sessdata: str = "") -> Any:
        raise ConnectionError("network down")

    monkeypatch.setattr(bilibili_module, "fetch_bilibili", raise_network)
    resp = _post_url(env)
    assert resp.status_code == 502
    assert "B 站" in resp.json()["detail"]


def test_url_import_no_api_key_rejected(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 API Key → 400(不调 fetch_bilibili)。"""
    from app.service.llm import resolve as resolve_module

    async def fake_resolve(session, owner_id):
        return ("http://fake/v1", "", "fake-model")

    monkeypatch.setattr(resolve_module, "resolve_llm_config", fake_resolve)
    called: list[bool] = []
    monkeypatch.setattr(
        bilibili_module, "fetch_bilibili",
        lambda *a, **kw: called.append(True) or _fake_bilibili_result(),
    )

    resp = _post_url(env)
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]
    assert called == []


def test_url_import_filename_sanitized(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """视频标题含非法字符 → 文件名净化后写入暂存。"""
    result = _fake_bilibili_result()
    result.info.title = "视频/标题:含*非法?字符"
    monkeypatch.setattr(bilibili_module, "fetch_bilibili", lambda url, *, sessdata="": result)

    resp = _post_url(env)
    assert resp.status_code == 201, resp.text
    staging = Path(env.calls[0]["file_path"])
    assert "/" not in staging.name
    assert ":" not in staging.name
    assert "*" not in staging.name
    assert "?" not in staging.name
    assert staging.suffix == ".md"


# ==================== whisper 异步模式测试 ====================


def test_whisper_import_async_returns_immediately(env) -> None:
    """whisper 模式：API 立即返回 201，不阻塞；enqueue_video_extract 被调用。"""
    resp = _post_url(env, mode="whisper")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduped"] is False
    assert body["job_status"] == "queued"
    assert body["source_id"] > 0
    assert body["job_id"] > 0

    # enqueue_video_extract 被调了一次（不是 enqueue_import）
    assert len(env.video_calls) == 1
    call = env.video_calls[0]
    assert call["mode"] == "whisper"
    assert call["source_id"] == body["source_id"]
    assert call["owner_id"] == 1
    # enqueue_import 没被调（whisper 走 video 队列）
    assert env.calls == []


def test_whisper_import_creates_stub_source(env) -> None:
    """whisper 模式：创建占位 sha 的 stub source + video_extract job。"""
    resp = _post_url(env, mode="whisper")
    body = resp.json()
    source_id = body["source_id"]

    async def check() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            # 占位 sha（不更新为真实 sha）
            assert source.sha256.startswith("video:")
            assert "whisper" in source.sha256
            assert source.size_bytes == 0  # stub，还没提取
            assert source.parse_status == "pending"
            # parse_meta 有视频元信息
            meta = source.parse_meta
            assert meta["video_mode"] == "whisper"
            assert meta["video_url"] is not None
            assert meta["video_stage"] == "排队中"
            # job 的 kind 是 import（视频提取也是一种 import）
            job = await repos.jobs.get_by_dedupe_key(f"import:{source.sha256}")
            assert job is not None
            assert job.kind == "import"

    env.client.portal.call(check)


def test_whisper_import_dedupe(env) -> None:
    """whisper 模式：同一 BVID 重复导入 → 同 source + job 复用（job 未完成→deduped=False 继续轮询）。"""
    first = _post_url(env, mode="whisper")
    assert first.status_code == 201
    second = _post_url(env, mode="whisper")
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is False
    assert body["source_id"] == first.json()["source_id"]


def test_whisper_import_done_job_dedupes(env) -> None:
    """whisper 模式：job 真完成过 → deduped=True,不再入队。"""
    first = _post_url(env, mode="whisper")
    assert first.status_code == 201
    source_id = first.json()["source_id"]

    async def mark_done() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            await repos.jobs.update_status(job.id, "done")
            await session.commit()

    env.client.portal.call(mark_done)

    second = _post_url(env, mode="whisper")
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is True
    assert body["source_id"] == source_id
    assert len(env.video_calls) == 1  # done → 不重复入队


def test_whisper_import_failed_job_retries(env) -> None:
    """whisper 模式：失败后重新导入 = 重试。deduped=False + job 重置 queued + 重新入队。

    这是用户反馈的真 bug:第一次因没配 cookies 失败,第二次被误报「之前导入过」。
    """
    first = _post_url(env, mode="whisper")
    assert first.status_code == 201
    source_id = first.json()["source_id"]

    async def mark_failed() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            await repos.jobs.update_status(job.id, "failed", last_error="抖音拒绝访问（Cookies 无效或过期）")
            await session.commit()

    env.client.portal.call(mark_failed)

    second = _post_url(env, mode="whisper")
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is False  # 重试不算去重
    assert body["source_id"] == source_id
    assert len(env.video_calls) == 2  # 重新入队了

    async def check_reset() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            assert job.status == "queued"
            assert job.last_error is None

    env.client.portal.call(check_reset)


def test_whisper_import_no_api_key_rejected(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """whisper 模式：没配 API Key → 400（不入队）。"""
    from app.service.llm import resolve as resolve_module

    async def fake_resolve(session, owner_id):
        return ("http://fake/v1", "", "fake-model")

    monkeypatch.setattr(resolve_module, "resolve_llm_config", fake_resolve)
    resp = _post_url(env, mode="whisper")
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]
    assert env.video_calls == []


def test_whisper_import_invalid_url(env) -> None:
    """whisper 模式：非 B 站链接 → 400。"""
    resp = _post_url(env, url="https://www.youtube.com/watch?v=abc", mode="whisper")
    assert resp.status_code == 400


def test_source_status_includes_video_stage(env) -> None:
    """GET /api/sources/{id} 返回 video_stage 字段。"""
    resp = _post_url(env, mode="whisper")
    body = resp.json()
    status = env.client.get(f"/api/sources/{body['source_id']}")
    assert status.status_code == 200
    detail = status.json()
    assert "video_stage" in detail
    assert detail["video_stage"] == "排队中"


# ==================== 抖音视频测试 ====================


_DOUYIN_URL = "https://v.douyin.com/uqCjtoCtXh4/"


def test_douyin_whisper_import_async(env) -> None:
    """抖音 whisper 模式：立即返回 201 + stub source（占位 sha video:douyin:*）。"""
    resp = _post_url(env, url=_DOUYIN_URL, mode="whisper")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduped"] is False
    assert body["job_status"] == "queued"
    assert body["source_id"] > 0

    assert len(env.video_calls) == 1
    call = env.video_calls[0]
    assert call["mode"] == "whisper"
    assert call["source_id"] == body["source_id"]
    # 抖音不传 sessdata（B站 cookie），cookies 由 worker 从设置页读取
    assert call["sessdata"] == ""
    assert env.calls == []  # 不走 enqueue_import


def test_douyin_whisper_stub_source_sha(env) -> None:
    """抖音 stub source 的 sha256 是占位值 video:douyin:*。"""
    resp = _post_url(env, url=_DOUYIN_URL, mode="whisper")
    body = resp.json()

    async def check() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(body["source_id"])
            assert source is not None
            assert source.sha256.startswith("video:douyin:")
            assert source.size_bytes == 0
            assert source.parse_meta["video_mode"] == "whisper"
            assert source.parse_meta["video_stage"] == "排队中"

    env.client.portal.call(check)


def test_douyin_subtitle_mode_rejected(env) -> None:
    """抖音 + subtitle 模式 → 400（抖音不支持字幕模式）。"""
    resp = _post_url(env, url=_DOUYIN_URL, mode="subtitle")
    assert resp.status_code == 400
    assert "字幕" in resp.json()["detail"] or "whisper" in resp.json()["detail"]
    assert env.video_calls == []


def test_douyin_whisper_dedupe(env) -> None:
    """抖音 whisper 模式：同一 URL 重复导入 → 同 source 复用（job 未完成→deduped=False）。"""
    first = _post_url(env, url=_DOUYIN_URL, mode="whisper")
    assert first.status_code == 201
    second = _post_url(env, url=_DOUYIN_URL, mode="whisper")
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is False
    assert body["source_id"] == first.json()["source_id"]


def test_douyin_whisper_failed_job_retries(env) -> None:
    """抖音：失败后重新导入 = 重试（用户反馈的真 bug 场景）。"""
    first = _post_url(env, url=_DOUYIN_URL, mode="whisper")
    assert first.status_code == 201
    source_id = first.json()["source_id"]

    async def mark_failed() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            await repos.jobs.update_status(job.id, "failed", last_error="抖音拒绝访问")
            await session.commit()

    env.client.portal.call(mark_failed)

    second = _post_url(env, url=_DOUYIN_URL, mode="whisper")
    assert second.status_code == 200
    body = second.json()
    assert body["deduped"] is False
    assert len(env.video_calls) == 2  # 重新入队

    async def check_reset() -> None:
        async with env.shared["factory"]() as session:
            from app.repo import Repos

            repos = Repos(session, 1)
            source = await repos.sources.get(source_id)
            assert source is not None
            job = await repos.jobs.get_by_dedupe_key("import:" + source.sha256)
            assert job is not None
            assert job.status == "queued"

    env.client.portal.call(check_reset)


def test_douyin_full_url_whisper(env) -> None:
    """抖音完整 URL（www.douyin.com/video/{id}）也能导入。"""
    resp = _post_url(env, url="https://www.douyin.com/video/7467293802585378048", mode="whisper")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_id"] > 0
