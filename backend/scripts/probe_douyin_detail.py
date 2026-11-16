"""诊断抖音 detail 响应:打印每个 aweme/detail 响应的顺序 + status_code + aweme_detail 是否非空。

确认假设:抖音是否先回一个 aweme_detail=null 的空壳,导致提取器捕到空响应。
venv python -I 跑,弹浏览器 ~20s。
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

SHORT = "https://v.douyin.com/L6s5RTXEh9M/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_DETAIL_MARK = "aweme/v1/web/aweme/detail"


def resolve_video_id(url: str) -> str:
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    resp = urllib.request.urlopen(req, timeout=15)
    m = re.search(r"/video/(\d+)", resp.url)
    return m.group(1) if m else "unknown"


def main() -> None:
    vid = resolve_video_id(SHORT)
    page_url = f"https://www.douyin.com/video/{vid}"
    print(f"video_id={vid}")

    detail_responses: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False, channel="msedge",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = browser.new_context(
                user_agent=_UA, viewport={"width": 1280, "height": 800}, locale="zh-CN"
            )
            ctx.add_init_script(
                'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            )
            page = ctx.new_page()

            def on_response(resp) -> None:  # noqa: ANN001
                if _DETAIL_MARK not in resp.url:
                    return
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    return
                aweme = data.get("aweme_detail")
                detail_responses.append(
                    {
                        "host": resp.url.split("/")[2],
                        "status_code": data.get("status_code"),
                        "aweme_detail_nonempty": isinstance(aweme, dict) and bool(aweme),
                        "aweme_detail_keys": list(aweme.keys())[:8] if isinstance(aweme, dict) else None,
                        "has_video": isinstance(aweme, dict) and "video" in aweme,
                    }
                )

            page.on("response", on_response)
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(18000)
        finally:
            browser.close()

    out = Path(__file__).parent / "douyin_probe_result.json"
    out.write_text(json.dumps(detail_responses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(detail_responses)} detail responses (in arrival order):\n")
    for i, d in enumerate(detail_responses):
        flag = "OK   " if d["aweme_detail_nonempty"] else "EMPTY"
        print(f"  [{i}] {flag} host={d['host']} status_code={d['status_code']}")
        print(f"      aweme_detail_keys={d['aweme_detail_keys']} has_video={d['has_video']}")
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main()
