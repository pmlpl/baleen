"""长会话渲染性能探针(2026-11-26,决定要不要上虚拟滚动)。

**要回答的问题**:重开一个 50 条消息的会话,首屏渲染要多久?用户等得住吗?
只有真测出来的数字才配决定「上不上虚拟滚动」——虚拟滚动会带来滚动定位、
搜索高亮、图表懒渲染时机等一串新复杂度,不能凭感觉上。

**测量方法(第一版错了,记下来)**:最初用 Playwright 往返夹时间
(``page.evaluate('performance.now()')`` → click → wait_for_function → 再取时间),
结果 200 条比 50 条还快、且零长任务 —— 因为**往返 IPC 噪声盖过了真实信号**。
现在改成**全在页面内计时**:注入 MutationObserver,点会话那一刻打 mark,
``.leaf`` 数量达到目标那一刻再打 mark,区间时长完全在浏览器里取,
不含任何 Playwright 往返。

**怎么造数据**:``page.route`` 拦 GET /api/qa/sessions/{id},返回合成 N 条消息。
内容长度按真实会话实测分布造(最长 52 条 / 26779 字 ≈ 每条 515 字;长回答含
小标题/列表/行内代码 —— 这些才是渲染成本大头)。

跑法(backend 目录,需 vite 5180 + 后端 8000):
    uv run --with playwright python scripts/probes/probe_qa_render_perf.py
"""

from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

CHROME = r"C:\Users\MOM\AppData\Local\ms-playwright\chromium-1243\chrome-win64\chrome.exe"
BASE = "http://localhost:5180"

# 真实会话实测:最长 52 条 / 26779 字 → 平均每条 ~515 字。
# 但那是均值,真实分布不均:助理消息长、用户短。这里按 4:1 造(80% 短/20% 长)。
SHORT_CHARS = 40
LONG_CHARS = 1400
LONG_EVERY = 5  # 每 5 条里有 1 条长回答

# 判定线:用户感知分界。100ms 内 = 无感;300ms 内 = 可接受;>500ms = 要处理
GOOD_MS = 300.0
BAD_MS = 500.0

# 页面内计时器:点会话时 start(),消息渲染够数时记录 delta。
# 全部在浏览器里跑,不含 Playwright 往返。
INPAGE_TIMER_JS = """
window.__perf = { start: 0, done: 0, expect: 0, longtasks: [] };
try {
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__perf.longtasks.push(Math.round(e.duration));
  }).observe({entryTypes: ['longtask']});
} catch (e) {}

window.__perfInit = (expect) => {
  window.__perf.expect = expect;
  window.__perf.done = 0;
  window.__perf.longtasks = [];
  // 判据必须同时满足 .leaf 够数 **且** markdown 已解析出元素(h2/code)。
  // 为什么不能只看 .leaf:若 markdown 是异步渲染的,门够数时正文可能还是空壳,
  // 那样量到的是「外壳时间」不是「用户能看到正文的时间」。
  // 本项目 renderMarkdown 是同步 computed(实读确认),但这里仍按更严的口径判,
  // 防止哪天改成异步后探针静默失效。
  const ready = () =>
    document.querySelectorAll('.leaf').length >= window.__perf.expect &&
    document.querySelectorAll('.leaf--answer h2, .leaf--answer code, .leaf--answer ul').length > 0;
  const obs = new MutationObserver(() => {
    if (ready() && window.__perf.done === 0) {
      window.__perf.done = performance.now();
      obs.disconnect();
    }
  });
  obs.observe(document.body, { childList: true, subtree: true, characterData: true });
};

window.__perfStart = () => {
  window.__perf.start = performance.now();
  window.__perf.done = 0;
};

window.__perfResult = () => ({
  ms: window.__perf.done > 0 ? window.__perf.done - window.__perf.start : -1,
  longtasks: window.__perf.longtasks,
  leafCount: document.querySelectorAll('.leaf').length,
  hasMarkdown: document.querySelectorAll('.leaf--answer h2').length,
});
"""


def make_messages(n: int) -> list[dict]:
    """造 n 条消息(用户/助手交替)。长回答混入 markdown 结构,接近真实渲染成本。"""
    out: list[dict] = []
    for i in range(n):
        is_user = i % 2 == 0
        idx = i // 2
        if is_user:
            body = f"第 {idx + 1} 个问题:为什么需要这样设计?" * max(1, SHORT_CHARS // 30)
        elif idx % LONG_EVERY == LONG_EVERY - 1:
            # 长回答:带小标题、列表、行内代码 —— 这些才是渲染成本的大头
            body = (
                f"## 第 {idx + 1} 个回答\n\n"
                f"先说结论:`foo` 与 `bar` 的区别在于**作用域**。\n\n"
                + "\n".join(f"- 第 {j} 点:这是一段解释性文字,用来制造真实渲染压力。" for j in range(12))
                + "\n\n"
                + ("这是一段较长的正文,重复以拉长内容。" * (LONG_CHARS // 20))
            )
        else:
            body = f"第 {idx + 1} 个回答:简短结论,大概这么长。" * max(1, SHORT_CHARS // 20)
        out.append(
            {
                "id": 800000 + i,
                "role": "user" if is_user else "assistant",
                "content": body,
                "citations": [],
                "model": None if is_user else "fake-model",
                "cost_tokens": 0,
                "cache_hit_rate": None,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "created_at": "2026-11-26T00:00:00+08:00",
            }
        )
    return out


# 长任务观测器:记录渲染期间所有 >50ms 的主线程阻塞
LONGTASK_JS = INPAGE_TIMER_JS


def main() -> int:
    results: list[tuple[int, float, list[int]]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.goto(f"{BASE}/login")
        page.get_by_label("用户名").fill("admin")
        page.locator("input.pw-input").fill("admin123")
        page.locator("form").get_by_role("button", name="登录", exact=True).click()
        page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
        page.add_init_script(LONGTASK_JS)

        # 拦会话详情:按 state["n"] 返回合成数据
        state = {"n": 10}

        def on_detail(route) -> None:
            # 只拦具体会话详情(/api/qa/sessions/<数字>),不拦列表
            url = route.request.url
            tail = url.split("/api/qa/sessions/")[-1].split("?")[0]
            if not tail.isdigit():
                route.continue_()
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"messages": make_messages(state["n"])}, ensure_ascii=False),
            )

        page.route("**/api/qa/sessions/*", on_detail)

        page.goto(f"{BASE}/qa")
        page.wait_for_selector(".session-list", timeout=20000)
        page.wait_for_timeout(1200)

        print("消息数   首屏(ms)   最长长任务(ms)   渲染元素      判定")
        print("-" * 72)
        for n in (10, 30, 50, 100, 200, 500):
            state["n"] = n
            # 先回空态(每次从干净状态点进去,避免复用上一轮 DOM)
            page.locator(".new-btn").click()
            page.wait_for_timeout(400)
            page.evaluate("(k) => window.__perfInit(k)", n)
            page.evaluate("() => window.__perfStart()")

            page.locator(".session-open").first.click()
            # 等页面内计时器记下完成时刻(最多 30s)
            page.wait_for_function("window.__perf.done > 0", timeout=30000)
            r = page.evaluate("() => window.__perfResult()")
            elapsed, longtasks = r["ms"], r["longtasks"]

            results.append((n, elapsed, longtasks))
            worst = max(longtasks) if longtasks else 0
            verdict = "无感" if elapsed < GOOD_MS else ("可接受" if elapsed < BAD_MS else "⚠ 需优化")
            print(
                f"{n:5d}   {elapsed:8.1f}   {worst:14d}   "
                f"leaves={r['leafCount']:4d} h2={r['hasMarkdown']:3d}  {verdict}"
            )

        browser.close()

    # ── 结论 ──
    print("\n" + "=" * 72)
    print("判定(50 条 = 当前真实最长会话量级;200/500 条 = 压力外推):")
    by_n = {n: (ms, lt) for n, ms, lt in results}
    for key in (50, 200, 500):
        if key in by_n:
            ms, lt = by_n[key]
            worst = max(lt) if lt else 0
            print(f"  {key:3d} 条: {ms:6.1f}ms,最长阻塞 {worst:3d}ms")
    if 50 in by_n:
        ms50, lt50 = by_n[50]
        worst50 = max(lt50) if lt50 else 0
        if ms50 < GOOD_MS and worst50 < 100:
            print("\n  → 50 条不需要虚拟滚动(远在无感区,上虚拟滚动是过度工程)。")
        elif ms50 < BAD_MS:
            print("\n  → 50 条暂不需要,但接近可接受上限;可先做轻量优化。")
        else:
            print("\n  → 50 条已需处理。")
    if 500 in by_n:
        ms500, lt500 = by_n[500]
        worst500 = max(lt500) if lt500 else 0
        if ms500 > BAD_MS or worst500 > 200:
            print(f"  → 500 条已超线({ms500:.0f}ms / 阻塞 {worst500}ms):到那个量级必须上虚拟滚动。")
        else:
            print(f"  → 500 条仍在线内({ms500:.0f}ms),虚拟滚动留待更大量级。")

    print("\n结论仅供拍板,本探针不改代码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
