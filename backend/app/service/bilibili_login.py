"""B 站扫码登录半自动获取 SESSDATA（2026-11-19 深夜立项，探针 v4 实测链路）。

背景：B 站浏览器兜底（Playwright+SESSDATA 过风控挑战）需要登录态门票，但让用户
F12 手抄 SESSDATA 进 .env 从未发生过（11-19 财富自由 5583 两次导入死于此）。

半自动方案（探针实测的打开方式，v1-v3 教训见注释）：
- bilibili.com 首页的「登录」入口是 **hover 型 popover**——真鼠标 click 反而 toggle 关闭
  （v3 教训），必须 hover 等引导卡出现；
- 引导卡里的「立即登录」点击后弹出登录弹窗，**默认就是扫码二维码 tab**（v4 实测：
  黑白点阵 +「扫描二维码登录」+ 密码/短信 tab）；
- 用户扫码确认后 SESSDATA 落 .bilibili.com 域 cookie——轮询抓住。

⚠️ 红线：SESSDATA 只给 Playwright 浏览器上下文用（HTTP 重放对 B 站风控无效，
TLS 指纹打分，实测过）。本模块全程真浏览器，不碰任何 HTTP API。

用法（worker 的 login_cb / API 端点）：同步函数，放 to_thread 里跑；
登录态落库用 ``save_bilibili_login``（app_settings 三列，落库即全进程热生效）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import AppSettings
from app.repo.settings import SettingsRepo


class BiliLoginError(RuntimeError):
    """扫码登录失败的基类（页面结构变化/风控挡住登录弹窗等）。"""


class BiliLoginCancelled(BiliLoginError):
    """用户直接关掉了浏览器窗口（主动取消）。"""


class BiliLoginTimeout(BiliLoginError):
    """等待扫码超时（浏览器窗口开着，二维码放着没人扫）。"""


@dataclass
class BiliLoginResult:
    """扫码登录的收获。uid 可能为空串（cookie 里没抓到 DedeUserID 时不阻塞）。"""

    sessdata: str
    uid: str
    logged_at: datetime


def qr_login_bilibili(timeout_s: float = 180.0) -> BiliLoginResult:
    """弹真浏览器 → 出二维码 → 等用户扫码 → 抓 SESSDATA。同步阻塞，放 to_thread 里跑。

    timeout_s 是「等扫码」的时长（二维码出现起算，B 站二维码有效期也约 3 分钟）；
    前置导航/点击另有各自小超时。用户关窗 → BiliLoginCancelled；没人扫 →
    BiliLoginTimeout；打不开页面/弹窗没出来 → BiliLoginError（可能风控或结构变化）。
    """
    from playwright.sync_api import sync_playwright

    from app.service.bilibili_extractor import _launch_browser, _UA

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 800}, locale="zh-CN")
            ctx.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
            page = ctx.new_page()
            page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=30000)

            # ① hover 登录入口（hover 型 popover——v3 教训：真鼠标 click 会 toggle 关闭）
            entry = page.locator(
                ".header-avatar-unlogin-entry, .header-avatar-unlogin-inner, .header-avatar-unlogin-wrap"
            ).first
            entry.hover(timeout=8000)

            # ② 轮询等引导卡的「立即登录」（v4 实测 1s 出现；8s 没出=结构变了）
            now_btn = None
            for _ in range(8):
                page.wait_for_timeout(1000)
                cand = page.get_by_text("立即登录", exact=True).first
                try:
                    if cand.is_visible():
                        now_btn = cand
                        break
                except Exception:  # noqa: BLE001 —— 元素可能尚未挂载
                    continue
            if now_btn is None:
                raise BiliLoginError("B 站登录引导卡没有出现（页面结构可能变了，或被风控拦截）")
            now_btn.click(timeout=5000)

            # ③ 轮询等登录弹窗（二维码元素/「扫码」文本，v4 实测 1s 出现；10s 没出=风控翻转）
            def _dialog_ready() -> bool:
                return page.evaluate(
                    """() => {
                        const qr = [...document.querySelectorAll('img,canvas')].some(el =>
                            /qr|QR/i.test(String(el.className)) || /qrcode|qr/i.test(el.src || ''));
                        const scan = [...document.querySelectorAll('div,span,p')]
                            .some(e => ((e.innerText || '').includes('扫码')));
                        return qr || scan;
                    }"""
                )

            ready = False
            for _ in range(10):
                page.wait_for_timeout(1000)
                try:
                    if _dialog_ready():
                        ready = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not ready:
                raise BiliLoginError("B 站登录弹窗没有出现（可能被风控拦截，稍后再试）")

            # ④ 等用户扫码：轮询 cookie 里的 SESSDATA（同时检测用户关窗）
            waited_ms = 0
            poll_ms = 1000
            while waited_ms < timeout_s * 1000:
                if browser.is_closed():
                    raise BiliLoginCancelled("浏览器窗口被关闭，已取消 B 站登录")
                try:
                    cookies = ctx.cookies("https://www.bilibili.com")
                except Exception:  # noqa: BLE001 —— 关窗瞬间的竞态
                    raise BiliLoginCancelled("浏览器窗口被关闭，已取消 B 站登录") from None
                sessdata = next(
                    (c["value"] for c in cookies if c["name"] == "SESSDATA" and c.get("value")), ""
                )
                if sessdata:
                    uid = next(
                        (c["value"] for c in cookies if c["name"] == "DedeUserID" and c.get("value")), ""
                    )
                    page.wait_for_timeout(1500)  # 让登录后的 cookie 全套落地再关
                    return BiliLoginResult(
                        sessdata=sessdata, uid=uid, logged_at=datetime.now(UTC)
                    )
                page.wait_for_timeout(poll_ms)
                waited_ms += poll_ms
            raise BiliLoginTimeout(f"等待扫码超时（{int(timeout_s)} 秒），已放弃本次登录")
        finally:
            if not browser.is_closed():
                browser.close()


async def save_bilibili_login(session: AsyncSession, owner_id: int, result: BiliLoginResult) -> None:
    """扫码结果落 app_settings 三列（只 flush，commit 由调用方负责——Repo 约定）。

    落库即热生效：前后端/worker 每次现读（resolve_bilibili_sessdata），无需重启。
    """
    row = await SettingsRepo(session, owner_id).get()
    if row is None:
        row = AppSettings(owner_id=owner_id)
        session.add(row)
    row.bilibili_sessdata = result.sessdata
    row.bilibili_sessdata_at = result.logged_at
    row.bilibili_uid = result.uid or None
    await session.flush()


__all__ = [
    "BiliLoginCancelled",
    "BiliLoginError",
    "BiliLoginResult",
    "BiliLoginTimeout",
    "qr_login_bilibili",
    "save_bilibili_login",
]
