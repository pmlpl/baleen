"""抖音视频提取器——Playwright 拦截详情接口，免 cookie。

原理（2026-11-14 实测）：
- 抖音网页版视频页自己会带合法签名调用 ``aweme/v1/web/aweme/detail``，
  响应 JSON 里有标题/作者/时长/播放地址；
- 用系统自带 Edge（Playwright channel=msedge）打开页面、拦截该响应，
  即可拿到全部元信息 + 播放地址——**不需要用户配置任何 cookie**；
- 播放地址（douyinvod CDN）用普通 HTTP + UA/Referer 就能下载，无需 yt-dlp。

为什么不用 yt-dlp：抖音接口已要求一整套浏览器 cookie（msToken 等），
yt-dlp 无法自动获取；用户手动复制完整 Cookie 头对普通用户太复杂（主人 2026-11-14 反馈）。

⚠️ 提取过程中会在用户桌面弹出一个浏览器窗口约 10 秒（有头模式，降低被反爬识别概率）。
"""

from __future__ import annotations

import re
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# 桌面版视频页 URL（分享短链会重定向到这里）
_VIDEO_PAGE_RE = re.compile(r"/video/(\d+)")
# 详情接口路径（页面自己调用，响应里有 aweme_detail）
_DETAIL_API_MARK = "aweme/v1/web/aweme/detail"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass
class DouyinVideo:
    """抖音视频元信息 + 已下载的视频文件。"""

    video_id: str
    title: str
    description: str
    author: str
    duration_s: int  # 秒
    video_path: Path


def resolve_video_id(url: str) -> str:
    """从抖音链接解析数字视频 ID。短链（v.douyin.com）跟随重定向展开。"""
    from app.service.douyin import extract_douyin_id

    m = _VIDEO_PAGE_RE.search(url)
    if m:
        return m.group(1)
    # 短链：跟随重定向
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        resp = urllib.request.urlopen(req, timeout=15)
        final = resp.url
        m = _VIDEO_PAGE_RE.search(final)
        if m:
            return m.group(1)
    except Exception:
        pass
    # 实在解不开，退回 hash 占位（提取流程会失败，但至少 dedupe 键稳定）
    return extract_douyin_id(url) or "unknown"


def _pick_play_url(aweme: dict) -> str:
    """从 aweme_detail 里挑一个播放地址（去水印：playwm → play）。"""
    video = aweme.get("video", {})
    bit_rate = video.get("bit_rate", [])
    play_addr = {}
    if bit_rate:
        play_addr = bit_rate[0].get("play_addr", {})
    if not play_addr:
        play_addr = video.get("play_addr", {})
    urls = play_addr.get("url_list", [])
    if not urls:
        raise RuntimeError("抖音响应里没有播放地址（页面结构可能变了）")
    return urls[0].replace("playwm", "play")


def _launch_browser(p):
    """依次尝试系统 Edge → 系统 Chrome → 默认 chromium（后者需要 playwright install）。"""
    last_err: Exception | None = None
    for channel in ("msedge", "chrome", None):
        try:
            kwargs = {"headless": False, "args": ["--disable-blink-features=AutomationControlled"]}
            if channel:
                kwargs["channel"] = channel
            return p.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(
        f"无法启动浏览器（Playwright）：{last_err}。"
        "请确认系统装有 Edge 或 Chrome，或执行 `playwright install chromium`。"
    )


def fetch_douyin_video(url: str, *, output_dir: Path | None = None) -> DouyinVideo:
    """打开视频页 → 拦截详情接口 → 下载视频到临时目录。同步（worker 在线程里调）。"""
    from playwright.sync_api import sync_playwright

    out_dir = output_dir or Path(tempfile.mkdtemp(prefix="douyin_dl_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    video_id = resolve_video_id(url)
    page_url = f"https://www.douyin.com/video/{video_id}"

    captured: dict = {}

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 800}, locale="zh-CN")
            ctx.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
            page = ctx.new_page()

            def on_response(resp) -> None:  # noqa: ANN001
                if _DETAIL_API_MARK in resp.url and "data" not in captured:
                    try:
                        captured["data"] = resp.json()
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(30):  # 最多等 15s 让页面自己调详情接口
                if "data" in captured:
                    break
                page.wait_for_timeout(500)
            cookies_note = ctx.cookies()
        finally:
            browser.close()

    if "data" not in captured:
        raise RuntimeError(
            "没能从抖音页面获取视频信息（页面结构可能变了，或视频不可见）。请把链接发给开发者核查。"
        )

    aweme = captured["data"].get("aweme_detail")
    if not aweme:
        items = captured["data"].get("item_list") or []
        aweme = items[0] if items else None
    if not aweme:
        raise RuntimeError("抖音详情响应格式变化（没有 aweme_detail），请把链接发给开发者核查。")

    desc = (aweme.get("desc") or "").strip()
    author = (aweme.get("author") or {}).get("nickname", "")
    duration_s = int((aweme.get("video") or {}).get("duration") or 0) // 1000
    title = desc.split("#")[0].strip() or f"抖音视频{video_id}"

    play_url = _pick_play_url(aweme)
    video_path = out_dir / f"douyin_{video_id}.mp4"
    _download(play_url, video_path)

    return DouyinVideo(
        video_id=video_id,
        title=title,
        description=desc,
        author=author,
        duration_s=duration_s,
        video_path=video_path,
    )


def _download(play_url: str, target: Path) -> None:
    """流式下载视频（douyinvod CDN：普通 HTTP + UA/Referer 即可，无需 cookie）。"""
    req = urllib.request.Request(
        play_url,
        headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp, target.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    if target.stat().st_size < 1024:
        raise RuntimeError(f"抖音视频下载异常（只有 {target.stat().st_size} 字节）")


__all__ = ["DouyinVideo", "fetch_douyin_video", "resolve_video_id"]
