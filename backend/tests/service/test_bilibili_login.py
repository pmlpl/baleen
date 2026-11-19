"""B站扫码登录半自动获取 SESSDATA 测试（全 mock，不弹浏览器、不联网）。

- qr_login_bilibili：成功（SESSDATA 落 cookie）/ 超时 / 用户关窗 三路
- video_extractor 的 bilibili_login_cb 钩子：凭证类失败 → 钩子返回新值 → 原地重试一次
- 无钩子时 BilibiliAuthRequired → 人话报错（指引扫码）

Mock 链（qr_login_bilibili 函数内 import，运行时从模块取 → monkeypatch 生效）：
- app.service.bilibili_extractor._launch_browser → FakeBrowser
- playwright.sync_api.sync_playwright → 假上下文管理器
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------- Fake Playwright ----------


class FakeEntry:
    def hover(self, timeout: float = 0) -> None:
        pass


class FakeBtn:
    def is_visible(self) -> bool:
        return True

    def click(self, timeout: float = 0) -> None:
        pass


class FakeLocator:
    def __init__(self, first: Any) -> None:
        self.first = first


class FakePage:
    def goto(self, url: str, **_kw: Any) -> None:
        pass

    def locator(self, _sel: str) -> FakeLocator:
        return FakeLocator(FakeEntry())

    def get_by_text(self, _t: str, *, exact: bool = False) -> FakeLocator:
        return FakeLocator(FakeBtn())

    def wait_for_timeout(self, ms: float) -> None:
        pass  # 不真睡——超时路径用 timeout_s 压小 + poll_ms 账面推进

    def evaluate(self, _js: str, *args: Any) -> Any:
        return True  # _dialog_ready → 登录弹窗已出现


class FakeCtx:
    def __init__(self, cookie_batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(cookie_batches)
        self._i = 0

    def add_init_script(self, _s: str) -> None:
        pass

    def new_page(self) -> FakePage:
        return FakePage()

    def cookies(self, _url: str) -> list[dict[str, Any]]:
        if not self._batches:
            return []
        batch = self._batches[min(self._i, len(self._batches) - 1)]
        self._i += 1
        return batch


class FakeBrowser:
    def __init__(self, ctx: FakeCtx, closed: bool = False) -> None:
        self._ctx = ctx
        self._closed = closed

    def new_context(self, **_kw: Any) -> FakeCtx:
        return self._ctx

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        pass


def _install(monkeypatch: pytest.MonkeyPatch, browser: FakeBrowser) -> None:
    import contextlib

    import app.service.bilibili_extractor as bili_extract

    @contextlib.contextmanager
    def fake_sync_playwright():
        yield object()  # p —— _launch_browser 被 patch，p 不被使用

    monkeypatch.setattr(bili_extract, "_launch_browser", lambda _p: browser)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", fake_sync_playwright)


_SESSDATA = "sd%2Cfake%3Dtoken"
_COOKIES_LOGGED_IN = [
    {"name": "buvid3", "value": "abc"},
    {"name": "SESSDATA", "value": _SESSDATA},
    {"name": "DedeUserID", "value": "12345"},
]


# ---------- qr_login_bilibili ----------


def test_qr_login_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """扫码成功：SESSDATA 出现在 cookie → 抓住并返回（带 uid）。"""
    from app.service.bilibili_login import qr_login_bilibili

    # 第一次轮询空（未登录），第二次已登录
    ctx = FakeCtx([[], _COOKIES_LOGGED_IN])
    _install(monkeypatch, FakeBrowser(ctx))

    result = qr_login_bilibili(timeout_s=180.0)
    assert result.sessdata == _SESSDATA
    assert result.uid == "12345"
    assert result.logged_at is not None


def test_qr_login_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """二维码放着没人扫 → 超时报错（文案含秒数）。"""
    from app.service.bilibili_login import BiliLoginTimeout, qr_login_bilibili

    ctx = FakeCtx([[]])  # 永远没有 SESSDATA
    _install(monkeypatch, FakeBrowser(ctx))

    with pytest.raises(BiliLoginTimeout, match="超时"):
        qr_login_bilibili(timeout_s=0.003)  # 3ms → 3 轮账面轮询即超时


def test_qr_login_cancelled_by_window_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户直接关掉浏览器窗口 → 友好取消（不是超时）。"""
    from app.service.bilibili_login import BiliLoginCancelled, qr_login_bilibili

    ctx = FakeCtx([[]])
    _install(monkeypatch, FakeBrowser(ctx, closed=True))

    with pytest.raises(BiliLoginCancelled, match="取消"):
        qr_login_bilibili(timeout_s=180.0)


# ---------- video_extractor 的 login_cb 钩子 ----------


def _fake_media(tmp_path):
    from app.service.bilibili_extractor import BilibiliMedia

    return BilibiliMedia(
        bvid="BV1qrlogin11",
        title="扫码登录测试",
        description="",
        author="UP主",
        duration_s=60,
        audio_path=tmp_path / "a.m4a",
        video_path=None,
    )


def _install_ytdlp_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """yt-dlp 元信息阶段 412 失败（进兜底的必经路）。"""
    import app.service.video_extractor as ve

    def fake_info(url, **_kw):
        raise RuntimeError("yt-dlp 失败: HTTP Error 412: Precondition Failed")

    monkeypatch.setattr(ve, "_fetch_video_info", fake_info)
    monkeypatch.setattr(ve, "extract_audio", lambda vp, od: od / "audio.wav")
    monkeypatch.setattr(ve, "transcribe_audio", lambda wav: [])


def test_login_cb_retries_fallback_with_fresh_sessdata(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """钩子链：兜底第一次 BilibiliAuthRequired（未配置）→ login_cb 拿新 SESSDATA
    → 原地重试第二次成功 → 导入继续。"""
    import app.service.video_extractor as ve
    from app.service.bilibili_extractor import BilibiliAuthRequired

    _install_ytdlp_failing(monkeypatch)
    media = _fake_media(tmp_path)

    calls = {"browser": 0, "cb": 0}

    def fake_browser(url, *, sessdata=""):
        calls["browser"] += 1
        if calls["browser"] == 1:
            raise BilibiliAuthRequired("未配置 SESSDATA")
        assert sessdata == "fresh-sd"  # 重试用的是钩子返回的新值
        return media

    monkeypatch.setattr("app.service.bilibili_extractor.fetch_bilibili_media", fake_browser)

    def fake_login_cb() -> str:
        calls["cb"] += 1
        return "fresh-sd"

    vc = ve.extract_video_content(
        "https://www.bilibili.com/video/BV1qrlogin11/", bilibili_login_cb=fake_login_cb
    )
    assert vc.title == "扫码登录测试"
    assert calls["browser"] == 2
    assert calls["cb"] == 1


def test_login_cb_not_called_for_non_auth_failure(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非凭证类失败（如 CDN 下载崩）→ 不触发扫码钩子（弹了也没用）。"""
    import app.service.video_extractor as ve
    from app.service.bilibili_extractor import BilibiliAuthRequired

    _install_ytdlp_failing(monkeypatch)

    def fake_browser(url, *, sessdata=""):
        raise RuntimeError("CDN 下载异常（只有 3 字节）")

    monkeypatch.setattr("app.service.bilibili_extractor.fetch_bilibili_media", fake_browser)

    def bad_cb() -> str:
        raise AssertionError("非凭证类失败不该触发扫码钩子")

    with pytest.raises(RuntimeError, match="CDN 下载异常"):
        ve.extract_video_content(
            "https://www.bilibili.com/video/BV1qrlogin11/", bilibili_login_cb=bad_cb
        )


def test_auth_required_without_cb_reports_friendly_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无钩子（如测试/无 worker 场景）→ 人话报错，指引扫码。"""
    import app.service.video_extractor as ve
    from app.service.bilibili_extractor import BilibiliAuthRequired

    _install_ytdlp_failing(monkeypatch)

    def fake_browser(url, *, sessdata=""):
        raise BilibiliAuthRequired("未配置 SESSDATA")

    monkeypatch.setattr("app.service.bilibili_extractor.fetch_bilibili_media", fake_browser)

    with pytest.raises(RuntimeError, match="扫码"):
        ve.extract_video_content("https://www.bilibili.com/video/BV1qrlogin11/")


def test_auth_required_after_cb_retry_failure_reports_network_hint(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫码成功但重试兜底仍未过（IP 信誉烧坏场景）→ 报换网络指引。"""
    import app.service.video_extractor as ve
    from app.service.bilibili_extractor import BilibiliAuthRequired

    _install_ytdlp_failing(monkeypatch)

    calls = {"browser": 0}

    def fake_browser(url, *, sessdata=""):
        calls["browser"] += 1
        if calls["browser"] == 1:
            raise BilibiliAuthRequired("SESSDATA 可能已过期")
        raise RuntimeError("B 站风控挑战未通过")

    monkeypatch.setattr("app.service.bilibili_extractor.fetch_bilibili_media", fake_browser)

    with pytest.raises(RuntimeError, match="换个网络"):
        ve.extract_video_content(
            "https://www.bilibili.com/video/BV1qrlogin11/", bilibili_login_cb=lambda: "fresh-sd"
        )
