"""QaView 拆分等价探针(拆分前行为基线,2026-11-26)。

**存在理由**:质检员 P1-4 要求二次拆分 QaView(侧栏会话列表 / SSE 发送循环),
沿用 C1 的「先测后拆」铁律 —— 没有等价网就不许动刀。本探针把**当前**行为钉成
可复跑的断言,拆完必须逐条仍绿;任一断言转红就是行为退化。

**为什么不用真 SSE**:真调 /api/qa/chat/stream 要烧 LLM token 且每次答案不同,
钉不住等价性。这里用 ``page.route`` 拦流式端点返回**固定的多帧 SSE**
(meta → status → delta×3 → done),能覆盖事件序与增量累加,又完全确定。

跑法(backend 目录):
    uv run --with playwright python <本文件>

前置:vite dev 在 5180、后端在 8000、账号 admin/admin123。
"""

from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

CHROME = r"C:\Users\MOM\AppData\Local\ms-playwright\chromium-1243\chrome-win64\chrome.exe"
BASE = "http://localhost:5180"

OK: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else FAIL).append(name)
    mark = "PASS " if cond else "FAIL "
    print(mark + name + (f"  [{detail}]" if detail else ""))


# ── 假的流式响应:固定五帧,覆盖 meta/status/delta×3/done ──
def sse_body(session_id: int = 1) -> str:
    frames = [
        (
            "meta",
            {
                "session_id": session_id,
                "model": "fake-model",
                "search_query": "",
                "user_message": {
                    "id": 900001,
                    "role": "user",
                    "content": "闭包是什么",
                    "citations": [],
                    "model": None,
                    "cost_tokens": 0,
                    "cache_hit_rate": None,
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "created_at": "2026-11-26T00:00:00+08:00",
                },
            },
        ),
        ("status", {"text": "正在检索笔记库…"}),
        ("delta", {"text": "闭包是"}),
        ("delta", {"text": "函数加"}),
        ("delta", {"text": "捕获的外层变量。"}),
        (
            "done",
            {
                "took_ms": 12,
                "assistant_message": {
                    "id": 900002,
                    "role": "assistant",
                    "content": "闭包是函数加捕获的外层变量。",
                    "citations": [],
                    "model": "fake-model",
                    "cost_tokens": 10,
                    "cache_hit_rate": None,
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "created_at": "2026-11-26T00:00:01+08:00",
                },
            },
        ),
    ]
    return "".join(
        f"event: {ev}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        for ev, payload in frames
    )


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 登录
        page.goto(f"{BASE}/login")
        page.get_by_label("用户名").fill("admin")
        page.locator("input.pw-input").fill("admin123")
        page.locator("form").get_by_role("button", name="登录", exact=True).click()
        page.wait_for_url(lambda u: "/login" not in u, timeout=20000)

        # ── 拦流式端点(默认成功;单独一例改成 500 测回滚) ──
        state = {"fail": False, "session": 1, "calls": 0}

        def on_stream(route) -> None:
            state["calls"] += 1
            if state["fail"]:
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body=json.dumps({"detail": "上游服务崩了"}),
                )
                return
            route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=sse_body(state["session"]),
            )

        page.route("**/api/qa/chat/stream", on_stream)

        page.goto(f"{BASE}/qa")
        page.wait_for_selector(".session-list, .side-empty", timeout=20000)
        page.wait_for_timeout(1200)

        # ══════════ A. 侧栏会话列表 ══════════
        print("\n--- A. 侧栏会话列表 ---")

        items = page.locator(".session-item")
        n_items = items.count()
        check("侧栏渲染了会话条目", n_items > 0, f"{n_items} 条")
        check("「+ 新对话」在顶部", page.locator(".new-btn").count() == 1)
        check("搜索框存在", page.locator(".session-search").count() == 1)

        first_title = page.locator(".session-title-text").first.inner_text()
        check("首条有标题", len(first_title) > 0, first_title[:20])
        check("条目带日期", page.locator(".session-date").first.inner_text() != "")

        # A1. 搜索过滤:输入一个不可能匹配的词 → 空态
        page.locator(".session-search").fill("zzz不存在的词zzz")
        page.wait_for_timeout(500)
        check(
            "搜索无匹配 → 显示「没有匹配的会话」",
            "没有匹配的会话" in page.locator(".session-side").inner_text(),
        )
        page.locator(".session-search").fill("")
        page.wait_for_timeout(400)
        check("清空搜索后列表恢复", page.locator(".session-item").count() == n_items)

        # A2. 搜索真匹配:取首条标题的第一个字过滤
        if len(first_title) > 0:
            needle = first_title.strip()[:1]
            page.locator(".session-search").fill(needle)
            page.wait_for_timeout(500)
            filtered = page.locator(".session-item").count()
            check("搜索命中后条数 ≤ 全部", 0 < filtered <= n_items, f"{filtered} / {n_items}")
            page.locator(".session-search").fill("")
            page.wait_for_timeout(400)

        # A3. 点击会话切换:URL 带上 ?session=N
        target_id = items.first.get_attribute("data-session-id")
        items.first.locator(".session-open").click()
        page.wait_for_timeout(1500)
        check("点会话后 URL 带 session 参数", f"session={target_id}" in page.url, page.url)
        check("点会话后该条高亮", "session-item--active" in (items.first.get_attribute("class") or ""))

        # A4. 双击标题 → 原地变输入框
        page.locator(".session-title-text").first.dblclick()
        page.wait_for_timeout(500)
        rename = page.locator(".session-rename-input")
        check("双击进入重命名", rename.count() == 1)
        if rename.count() == 1:
            check("重命名输入框自动聚焦", rename.evaluate("el => el === document.activeElement"))
            old_val = rename.input_value()
            check("重命名框带入原标题", old_val == first_title.strip(), old_val[:20])
            # Esc 取消
            rename.press("Escape")
            page.wait_for_timeout(400)
            check("Esc 取消重命名", page.locator(".session-rename-input").count() == 0)
            check("取消后标题未变", page.locator(".session-title-text").first.inner_text() == first_title)

        # A5. 右键 → 菜单三项
        items.first.click(button="right")
        page.wait_for_timeout(500)
        menu = page.locator(".row-menu")
        check("右键弹出菜单", menu.count() == 1)
        if menu.count() == 1:
            labels = [b.inner_text().strip() for b in page.locator(".row-menu-item").all()]
            check("菜单三项(重命名/导出/删除)", len(labels) == 3, " | ".join(labels))
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            check("Esc 关闭右键菜单", page.locator(".row-menu").count() == 0)

        # A6. 新建对话 → 空态
        page.locator(".new-btn").click()
        page.wait_for_timeout(800)
        check("新建对话后进入空态", page.locator(".qa-empty").count() == 1)
        check("空态标题是「问点什么吧」", "问点什么吧" in page.locator(".qa-empty").inner_text())

        # ══════════ B. SSE 发送循环 ══════════
        print("\n--- B. SSE 发送循环 ---")

        state["session"] = int(target_id or 1)
        page.locator(".qa-input").fill("闭包是什么")
        page.locator(".chat-input").get_by_role("button", name="发送").click()

        # B1. 乐观上屏:用户气泡立刻出现(不等 done)
        page.wait_for_timeout(250)
        check("用户消息乐观上屏", page.locator(".leaf--note").count() == 1)
        check("乐观气泡立刻清空输入框", page.locator(".qa-input").input_value() == "")

        # B2. 流式结束后:一条助手气泡、正文完整
        page.wait_for_timeout(1500)
        check("助手气泡出现", page.locator(".leaf--answer").count() == 1)
        answer = page.locator(".leaf--answer").first.inner_text()
        check("答案正文完整(三帧累加)", "闭包是函数加捕获的外层变量。" in answer, answer[:60])
        check("发送完成后「发送」键恢复可用", not page.locator(".chat-input button[type=submit]").is_disabled())
        check("流结束后不再显示「思考中」", "思考中" not in page.locator(".chat-main").inner_text())

        # ══════════ C. 发送失败回滚 ══════════
        print("\n--- C. 发送失败回滚 ---")

        before_notes = page.locator(".leaf--note").count()
        before_answers = page.locator(".leaf--answer").count()
        state["fail"] = True
        page.locator(".qa-input").fill("这条会失败")
        page.locator(".chat-input").get_by_role("button", name="发送").click()
        page.wait_for_timeout(1800)

        check("失败后显示错误提示", page.locator(".note--error").count() > 0)
        check("失败后输入框还原原文", page.locator(".qa-input").input_value() == "这条会失败")
        check(
            "失败后乐观气泡已撤销",
            page.locator(".leaf--note").count() == before_notes,
            f"{page.locator('.leaf--note').count()} vs {before_notes}",
        )
        check(
            "失败后不残留空气泡",
            page.locator(".leaf--answer").count() == before_answers,
            f"{page.locator('.leaf--answer').count()} vs {before_answers}",
        )
        state["fail"] = False

        # ══════════ D. 健康度 ══════════
        print("\n--- D. 健康度 ---")
        check("全程零 JS 报错", len(errors) == 0, "; ".join(errors)[:200])
        check("流式端点被调用 2 次(成功+失败各一)", state["calls"] == 2, str(state["calls"]))

        browser.close()

    print(f"\n{'=' * 56}")
    print(f"等价探针:{len(OK)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    print("行为基线已钉住,可以开始拆分。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
