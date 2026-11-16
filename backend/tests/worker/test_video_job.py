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

    def fake_extract(url, *, sessdata="", cookies_str="", client=None, progress_cb=None):
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
        def boom(url, *, sessdata="", cookies_str="", client=None, progress_cb=None):
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


def test_resolve_cookie_file_priority(tmp_path: Path) -> None:
    """cookies_file（现成 Netscape 文件）> cookies_str > sessdata。"""
    from app.service.video_extractor import _resolve_cookie_file

    existing = tmp_path / "harvested.txt"
    existing.write_text("# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tx\n", encoding="utf-8")

    # cookies_file 存在 → 直接用，不解析别的
    cf = _resolve_cookie_file(sessdata="sd", cookies_str="a=b", cookies_file=str(existing), output_dir=tmp_path)
    assert cf == str(existing)

    # cookies_file 不存在 → 退回 cookies_str
    cf2 = _resolve_cookie_file(
        sessdata="sd", cookies_str="a=b", cookies_file=str(tmp_path / "nope.txt"), output_dir=tmp_path
    )
    text2 = Path(cf2).read_text(encoding="utf-8")
    assert "a" in text2 and "b" in text2

    # 只有 sessdata → SESSDATA cookie
    cf3 = _resolve_cookie_file(sessdata="sd", output_dir=tmp_path)
    assert "SESSDATA" in Path(cf3).read_text(encoding="utf-8")


# ---------- B站风控兜底（Playwright 收获 cookie → yt-dlp 重试）----------


def test_extract_video_content_bilibili_fallback_on_412(tmp_path: Path, monkeypatch) -> None:
    """B站 yt-dlp 失败（412 风控）→ 浏览器提取兜底（bilibili_extractor）→ 成功。"""
    import app.service.video_extractor as ve

    calls: dict[str, int] = {"info": 0, "browser": 0}

    def fake_info(url, *, sessdata="", cookies_str="", cookies_file=""):
        calls["info"] += 1
        raise RuntimeError("yt-dlp 元信息提取失败: HTTP Error 412: Precondition Failed")

    fake_media = type(
        "BilibiliMedia", (),
        {
            "bvid": "BV1wFZ8YBEt4",
            "title": "吃盐科普",
            "description": "为什么人类需要不断吃盐",
            "author": "行者道荣",
            "duration_s": 198,
            "audio_path": tmp_path / "fake.m4a",
            "video_path": None,  # 浏览器兜底视频流失败 → 帧提取跳过
        },
    )()

    def fake_browser_fetch(url, *, sessdata="", output_dir=None):
        calls["browser"] += 1
        assert sessdata == "test-sd"  # SESSDATA 透传给浏览器提取器
        return fake_media

    monkeypatch.setattr(ve, "_fetch_video_info", fake_info)
    monkeypatch.setattr("app.service.bilibili_extractor.fetch_bilibili_media", fake_browser_fetch)
    monkeypatch.setattr(ve, "extract_audio", lambda vp, od: od / "audio.wav")
    monkeypatch.setattr(ve, "transcribe_audio", lambda wav: [{"start": 0.0, "end": 1.0, "text": "盐"}])

    vc = ve.extract_video_content(
        "https://www.bilibili.com/video/BV1wFZ8YBEt4/",
        sessdata="test-sd",
    )
    assert vc.title == "吃盐科普"
    assert vc.owner_name == "行者道荣"
    assert "盐" in vc.markdown
    assert calls["browser"] == 1
    assert calls["info"] == 1  # generic 只试一次，失败即进兜底


def test_extract_video_content_bilibili_no_fallback_for_duration(tmp_path: Path, monkeypatch) -> None:
    """时长超限是确定性错误 → 不触发浏览器兜底。"""
    import app.service.video_extractor as ve

    monkeypatch.setattr(
        ve, "_fetch_video_info",
        lambda url, **kw: {"title": "长视频", "duration": 7200, "uploader": "",
                           "description": "", "webpage_url": url},
    )

    def _no_browser(url, *, sessdata="", output_dir=None):
        raise AssertionError("时长超限不该触发兜底")

    monkeypatch.setattr("app.service.bilibili_extractor.fetch_bilibili_media", _no_browser)

    with pytest.raises(RuntimeError, match="超过 whisper 模式"):
        ve.extract_video_content("https://www.bilibili.com/video/BV1GJ411x7h7/")


# ---------- dHash 去重 + 嵌图标记（场景检测抽帧） ----------


def test_dhash_dedup_removes_near_duplicates(tmp_path: Path) -> None:
    """dHash 去重：同一幻灯片（近重复帧）只留 1 张。

    纯色图 dHash 全 0 无法区分，用「左半色 A 右半色 B」的纹理图测：
    两张相同纹理 → 同 hash → 去重；左右翻转的纹理 → hash 不同 → 保留。
    """
    from PIL import Image, ImageDraw

    from app.service.video_extractor import _dedup_by_dhash, _dhash, _hamming

    def two_color(left, right):
        img = Image.new("RGB", (64, 64), right)
        ImageDraw.Draw(img).rectangle([0, 0, 31, 63], fill=left)
        return img

    a1, a2, b = tmp_path / "a1.jpg", tmp_path / "a2.jpg", tmp_path / "b.jpg"
    two_color((255, 0, 0), (0, 0, 0)).save(a1)  # 左红右黑
    two_color((255, 0, 0), (0, 0, 0)).save(a2)  # 同上 → 同 hash
    two_color((0, 0, 0), (255, 0, 0)).save(b)   # 左黑右红（翻转）→ hash 不同
    assert _dhash(a1) == _dhash(a2)
    assert _hamming(_dhash(a1), _dhash(b)) > 5

    # 去重：a2 与 a1 近重复 → 只留 a1 + b
    frames = [(0.0, a1), (10.0, a2), (20.0, b)]
    deduped = _dedup_by_dhash(frames)
    assert len(deduped) == 2
    assert deduped[0][1] == a1
    assert deduped[1][1] == b


def test_build_video_markdown_embeds_image_markers() -> None:
    """build_video_markdown 产出 baleen-image:N 嵌图标记 + 文字层，而非旧的文字描述。"""
    from app.service.video_extractor import FrameContent, build_video_markdown

    md = build_video_markdown(
        title="测试视频",
        description="",
        owner_name="UP",
        duration=60,
        transcript_segments=[{"start": 0.0, "end": 5.0, "text": "这是第一段字幕"}],
        frame_contents=[
            FrameContent(
                timestamp=2.0, alt="代码截图", text_layer="```python\nprint('hi')\n```", media_key="abc123"
            )
        ],
        url="https://example.com/v",
    )
    # 嵌图标记（不是旧的 "> 📷 画面：文字"）
    assert "![[0:02] 代码截图](baleen-image:0)" in md
    assert "📷" not in md  # 旧文字描述标记已移除
    # 文字层（逐字转录的代码）在图标记前
    assert md.index("print('hi')") < md.index("baleen-image:0")
    # 转写正文仍在
    assert "这是第一段字幕" in md


def test_dhash_crop_bottom_ignores_subtitle_changes(tmp_path: Path) -> None:
    """字幕条差异不该让同一画面判成不同帧（SpaceX 实测：帧1/帧2 距离 8→裁25%后1）。

    构造：上半部分相同白板内容（固定种子噪声，模拟画面纹理）+ 底部字幕带用不同种子的
    噪声（模拟逐句变化的字幕）。裁掉底部后两帧应判为近重复 → 去重掉后者。
    dHash 比较相邻像素，纯色块全 0 bit 无区分度，所以用噪声而非纯色。
    """
    import random

    from PIL import Image

    from app.service.video_extractor import _dedup_by_dhash, _dhash, _hamming

    def frame_with_subtitle_band(subtitle_seed: int) -> Image.Image:
        img = Image.new("RGB", (128, 72), (255, 255, 255))
        px = img.load()
        rng = random.Random(0)  # 上半「白板内容」固定 → 两帧一致
        for y in range(0, 54):
            for x in range(0, 128):
                v = rng.randint(0, 255)
                px[x, y] = (v, v, v)
        rng2 = random.Random(subtitle_seed)  # 底部字幕带 → 逐帧不同
        for y in range(56, 72):
            for x in range(0, 128):
                v = rng2.randint(0, 255)
                px[x, y] = (v, v, v)
        return img

    a, b = tmp_path / "sub_a.jpg", tmp_path / "sub_b.jpg"
    frame_with_subtitle_band(1).save(a)
    frame_with_subtitle_band(2).save(b)

    # 不裁底部 → 字幕差异让 hash 距离大，不去重
    d_full = _hamming(_dhash(a), _dhash(b))
    kept_no_crop = _dedup_by_dhash([(0.0, a), (10.0, b)], crop_bottom=0.0)
    assert len(kept_no_crop) == 2, f"未裁底部时应保留 2 帧（距离 {d_full}）"
    # 裁掉底部字幕条 → 判为近重复，只留第一帧
    d_crop = _hamming(_dhash(a, crop_bottom=0.25), _dhash(b, crop_bottom=0.25))
    kept_crop = _dedup_by_dhash([(0.0, a), (10.0, b)], crop_bottom=0.25)
    assert len(kept_crop) == 1, f"裁底部后应去重（距离 {d_crop}）"


def test_parse_frame_output_fallback_formats() -> None:
    """_parse_frame_output：标准格式解析出 text_layer+desc+信息密度；不守格式时整体当 desc。"""
    from app.service.video_extractor import _parse_frame_output

    tl, desc, info = _parse_frame_output(
        "INFO: high\n\nTEXT_LAYER:\nprint('hi')\n\nDESC:\n一个代码编辑器"
    )
    assert tl == "print('hi')"
    assert desc == "一个代码编辑器"
    assert info is True

    tl2, desc2, info2 = _parse_frame_output("只有一句描述，没有遵守格式")
    assert tl2 == ""
    assert desc2 == "只有一句描述，没有遵守格式"
    assert info2 is True  # 解析不出 INFO → 保守当有信息（宁留勿删）

    tl3, desc3, info3 = _parse_frame_output("TEXT_LAYER:\n无\n\nDESC:\n纯人物说话画面")
    assert tl3 == ""  # 「无」→ 空
    assert desc3 == "纯人物说话画面"
    assert info3 is True


def test_parse_frame_output_low_info_detection() -> None:
    """INFO: low（表情包/黑屏/纯标题卡）→ is_informative=False。"""
    from app.service.video_extractor import _parse_frame_output

    tl, desc, info = _parse_frame_output(
        "INFO: low\n\nTEXT_LAYER:\n无\n\nDESC:\n挠头的卡通表情包，表达焦虑"
    )
    assert info is False
    assert tl == ""
    assert desc == "挠头的卡通表情包，表达焦虑"

    # 小写/中文冒号也认
    _tl, _desc, info2 = _parse_frame_output("INFO：low\n\nTEXT_LAYER:\n无\n\nDESC:\n纯黑屏")
    assert info2 is False


def test_low_info_frames_are_dropped(monkeypatch, tmp_path) -> None:
    """transcribe_frames：INFO=low 且无文字层的帧被丢弃（不 store_media、不进结果）。

    主人反馈：22 分钟视频 30 帧里 5-6 张表情包/黑屏过场零信息，撑长笔记。
    """
    from PIL import Image

    import app.service.media as media_module
    from app.service.video_extractor import transcribe_frames

    monkeypatch.setattr(media_module, "MEDIA_ROOT", tmp_path / "media")

    # 两张帧文件
    frames = []
    for i in range(2):
        p = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (16, 16), (i * 100, 0, 0)).save(p)
        frames.append((float(i * 10), p))

    replies = [
        "INFO: high\n\nTEXT_LAYER:\n```python\nx = 1\n```\n\nDESC:\n代码编辑器",
        "INFO: low\n\nTEXT_LAYER:\n无\n\nDESC:\n卡通表情包，表达焦虑",
    ]

    class FakeClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, **_kw):
            r = replies[self.i]
            self.i += 1
            from types import SimpleNamespace

            return SimpleNamespace(content=r)

    out = transcribe_frames(frames, client=FakeClient())
    assert len(out) == 1  # 低信息帧被丢
    assert out[0].alt == "代码编辑器"
    assert "x = 1" in out[0].text_layer
    assert out[0].media_key  # 留下的帧存了字节

    # 低信息帧的字节没落盘：media 目录只有 1 个文件
    media_files = [p for p in (tmp_path / "media").rglob("*") if p.is_file()]
    assert len(media_files) == 1


def test_extract_scene_frames_caps_at_max() -> None:
    """MAX_SCENE_FRAMES 上限值钉死（改它要同步评估成本：每帧一次 DeepSeek-vision 调用）。"""
    from app.service.video_extractor import MAX_SCENE_FRAMES

    assert MAX_SCENE_FRAMES == 30


# ---------- 抖音 Playwright 路径（mock，不弹浏览器）----------


def test_extract_video_content_routes_douyin_to_playwright(tmp_path: Path, monkeypatch) -> None:
    """抖音 URL → 走 douyin_extractor（免 cookie），不走 yt-dlp。"""
    import app.service.video_extractor as ve

    fake_dv = type(
        "DV", (),
        {
            "title": "博弈论深度解读",
            "description": "读懂策略互动",
            "author": "向不惑",
            "duration_s": 1500,
            "video_path": tmp_path / "fake.mp4",
        },
    )()

    calls: dict[str, Any] = {}
    frame_calls: list[dict] = []

    def fake_fetch(url, *, output_dir=None, max_duration_s=5400, progress_cb=None):
        calls["url"] = url
        return fake_dv

    monkeypatch.setattr(ve, "extract_audio", lambda vp, od: od / "audio.wav")
    monkeypatch.setattr(ve, "transcribe_audio", lambda wav: [{"start": 0.0, "end": 1.0, "text": "内容"}])
    monkeypatch.setattr(
        ve, "_extract_frames",
        lambda vp, od, cli: frame_calls.append({"video_path": vp, "client": cli}) or [],
    )
    monkeypatch.setattr("app.service.douyin_extractor.fetch_douyin_video", fake_fetch)

    # 传 dummy client 才会走帧提取分支（无 client 时帧提取整个跳过）
    vc = ve.extract_video_content("https://v.douyin.com/UMeLLjAlXa8/", client=object())
    assert "v.douyin.com" in calls["url"]
    # 帧提取走场景检测新逻辑：video_path 透传给 _extract_frames（25 分钟视频）
    assert frame_calls and frame_calls[0]["video_path"] == tmp_path / "fake.mp4"
    assert vc.title == "博弈论深度解读"
    assert vc.owner_name == "向不惑"
    assert vc.duration == 1500
    assert "# 博弈论深度解读" in vc.markdown
    assert "作者: 向不惑" in vc.markdown


def test_extract_video_content_bilibili_still_ytdlp(tmp_path: Path, monkeypatch) -> None:
    """B站 URL → 仍走 yt-dlp 路径（_fetch_video_info）。"""
    import app.service.video_extractor as ve

    def fake_info(url, *, sessdata="", cookies_str="", cookies_file=""):
        return {
            "title": "B站视频", "duration": 100,
            "uploader": "UP主", "description": "", "webpage_url": url,
        }

    monkeypatch.setattr(ve, "_fetch_video_info", fake_info)
    monkeypatch.setattr(ve, "download_audio", lambda url, **kw: tmp_path / "a.m4a")
    monkeypatch.setattr(ve, "extract_audio", lambda vp, od: od / "audio.wav")
    monkeypatch.setattr(ve, "transcribe_audio", lambda wav: [])
    # 无 client → 跳过帧提取，download_video 不会被调
    vc = ve.extract_video_content("https://www.bilibili.com/video/BV1GJ411x7h7/")
    assert vc.title == "B站视频"


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
