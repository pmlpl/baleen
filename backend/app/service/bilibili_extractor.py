"""B站浏览器提取器——风控兜底的确定性方案（2026-11-14 深夜）。

背景：B站对「非浏览器指纹」的请求做页面级 412 风控（sha256 挑战 + gaia 指纹注册
ExClimbWuzhi）。实测发现：**yt-dlp 换上浏览器收获的 cookie 仍会概率性 412**——
B站对 TLS 指纹打分，cookie 迁不移过去。

确定性方案（douyin_extractor 同款模式）：Playwright（系统 Edge）+ SESSDATA 打开视频页，
过挑战后页面里直接有全部数据——
- ``window.__INITIAL_STATE__``：标题/作者/时长（SSR）
- ``window.__playinfo__``：dash 音频/视频流的 CDN 地址（token 签名，x-expires）
CDN 下载只需 UA + Referer，与客户端指纹无关。

需要 .env 的 BILIBILI_SESSDATA（裸 Playwright 会被升级到验证码，实测过不去）。
"""

from __future__ import annotations

import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass
class BilibiliMedia:
    """B站视频元信息 + 已下载的音频（视频可选，供帧提取）。"""

    bvid: str
    title: str
    description: str
    author: str
    duration_s: int
    audio_path: Path
    video_path: Path | None  # 下载失败时为 None（帧提取跳过）


def _launch_browser(p):
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


def _cdn_download(stream_url: str, target: Path) -> None:
    """B站 CDN（upos）直连下载：UA + Referer 即可，无需 cookie/指纹。"""
    req = urllib.request.Request(
        stream_url, headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp, target.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    if target.stat().st_size < 1024:
        raise RuntimeError(f"CDN 下载异常（只有 {target.stat().st_size} 字节）")


def fetch_bilibili_media(url: str, *, sessdata: str = "", output_dir: Path | None = None) -> BilibiliMedia:
    """打开视频页 → 解析 INITIAL_STATE/playinfo → CDN 下载音频（视频尽力而为）。同步。"""
    if not sessdata:
        raise RuntimeError(
            "B 站风控拦截了自动访问，且本账号没配置 SESSDATA。"
            "请在 .env 的 BILIBILI_SESSDATA 填入登录 cookie"
            "（bilibili.com → F12 → Application → Cookies → SESSDATA 的值），保存后重试导入。"
        )

    from app.service.bilibili import extract_bvid

    bvid = extract_bvid(url) or "unknown"
    out_dir = output_dir or Path(tempfile.mkdtemp(prefix="bilibili_dl_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 800}, locale="zh-CN")
            ctx.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
            ctx.add_cookies(
                [{"name": "SESSDATA", "value": sessdata, "domain": ".bilibili.com", "path": "/"}]
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # 挑战流程（412→JS→301→200）自行完成；重定向期间 content()/evaluate 会抛错，容错轮询
            video_data = None
            playinfo = None
            for _ in range(30):
                try:
                    video_data = page.evaluate(
                        "() => (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.videoData)"
                        " ? JSON.parse(JSON.stringify(window.__INITIAL_STATE__.videoData)) : null"
                    )
                    playinfo = page.evaluate("() => JSON.parse(JSON.stringify(window.__playinfo__ || null))")
                    if video_data and playinfo:
                        break
                except Exception:  # noqa: BLE001 —— 页面正在跳转（挑战重定向）
                    pass
                page.wait_for_timeout(1000)
        finally:
            browser.close()

    if not video_data or not playinfo:
        raise RuntimeError(
            "B 站风控挑战未通过（可能弹出了验证码）。"
            "请在自己的浏览器里手动打开该视频页通过一次验证，或稍后重试导入。"
        )

    title = (video_data.get("title") or f"bilibili_{bvid}").strip()
    desc = (video_data.get("desc") or "").strip()
    author = (video_data.get("owner") or {}).get("name", "")
    duration_s = int(video_data.get("duration") or 0)

    dash = (playinfo.get("data") or {}).get("dash") or {}
    audios = dash.get("audio") or []
    videos = dash.get("video") or []
    if not audios:
        raise RuntimeError("B站 playinfo 里没有音频流（页面结构可能变了）")

    audio_path = out_dir / f"bilibili_{bvid}.m4a"
    au = (audios[0].get("baseUrl") or audios[0].get("base_url") or "").replace("&amp;", "&")
    _cdn_download(au, audio_path)

    video_path: Path | None = None
    if videos:
        try:
            vv = videos[-1]  # 最后一个通常是最低画质——帧提取够用、体积小
            vu = (vv.get("baseUrl") or vv.get("base_url") or "").replace("&amp;", "&")
            video_path = out_dir / f"bilibili_{bvid}_video.mp4"
            _cdn_download(vu, video_path)
        except Exception:  # noqa: BLE001 —— 视频流失败不影响转写，帧提取跳过
            video_path = None

    return BilibiliMedia(
        bvid=bvid,
        title=title,
        description=desc,
        author=author,
        duration_s=duration_s,
        audio_path=audio_path,
        video_path=video_path,
    )


__all__ = ["BilibiliMedia", "fetch_bilibili_media"]
