#!/usr/bin/env python3
"""doc_lint —— baleen 文档一致性检查。

为什么存在：文档漂移从来不是「忘了写」，而是「写错了没人发现」。
把可机械判定的一致性做成 0/1，AI 交付前跑一次，漂移当场暴露，
而不是等三周后某个会话打开 README 发现基线还是 334。

用法：
    python scripts/doc_lint.py            # 跑全部检查
    python scripts/doc_lint.py --quiet    # 只输出问题
退出码：有 ERROR → 1；只有 WARN/INFO → 0。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT.parent / "日志"

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"

results: list[tuple[str, str, str]] = []


def add(level: str, check: str, msg: str) -> None:
    results.append((level, check, msg))


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- 1. 契约版本
def check_api_version() -> None:
    text = read("docs/api.md")
    if not text:
        add(ERROR, "契约版本", "docs/api.md 不存在")
        return
    head = re.search(r"^#\s*API\s*契约\s*v(\d+)", text, re.M)
    rows = re.findall(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*v(\d+)\s*\|", text, re.M)
    if not head:
        add(ERROR, "契约版本", "docs/api.md 头部没有『# API 契约 vN』")
        return
    if not rows:
        add(WARN, "契约版本", "docs/api.md 变更记录里没找到『| 日期 | vN |』格式的行")
        return
    head_v, latest = int(head.group(1)), max(int(x) for x in rows)
    if head_v != latest:
        add(ERROR, "契约版本", f"头部 v{head_v} ≠ 变更记录最新 v{latest}（改头部，别改记录）")
    else:
        add(INFO, "契约版本", f"v{head_v} 与变更记录一致")


# ------------------------------------------------- 2. 测试基线跨文档必须一致
# 约定：声明当前基线只有一种写法 —— `基线 **371 passed**`。
# 统一写法是「颗粒度对齐」的前提：机器只认一种格式，人才不会各写各的。
# 历史交付块里那些「62 passed」「误拒率基线 53.3%」不算，它们不带这个形状。
BASELINE_RE = re.compile(r"基线\s*\*\*(\d{2,4})\s*passed\*\*")


def check_test_baseline() -> None:
    docs = [
        "STATUS.md",
        "README.md",
        "HANDOFF.md",
        "SESSION-HANDOFF.md",
    ]
    found: dict[str, set[int]] = {}
    for rel in docs:
        nums = {int(n) for n in BASELINE_RE.findall(read(rel))}
        if nums:
            found[rel] = nums

    if not found:
        add(WARN, "测试基线", "四份文档里都没有『基线 N』，无从比对")
        return

    if "STATUS.md" not in found:
        add(ERROR, "测试基线", "STATUS.md 速览里没有『基线 N』（它是唯一真源）")
        return

    truth = found["STATUS.md"]
    if len(truth) > 1:
        add(
            ERROR,
            "测试基线",
            f"STATUS 内部声称了多个当前基线：{sorted(truth)}（轨迹历史别用『基线』二字）",
        )

    base = max(truth)
    for rel, nums in found.items():
        if rel == "STATUS.md":
            continue
        bad = sorted(n for n in nums if n != base)
        if bad:
            add(ERROR, "测试基线", f"{rel} 写 {bad}，STATUS 是 {base}")
        else:
            add(INFO, "测试基线", f"{rel} 与基线 {base} 一致")


# ------------------------------------------------------- 3. 端点数量（若写了）
def check_endpoint_count() -> None:
    routers = list((ROOT / "backend" / "app" / "api").glob("*.py"))
    actual = 0
    for p in routers:
        actual += len(re.findall(r"@router\.(get|post|put|patch|delete)\(", p.read_text(encoding="utf-8")))

    text = read("STATUS.md")
    m = re.search(r"(\d+)\s*个[:：]", text)
    if not m:
        add(INFO, "端点数量", f"文档没写总数（实际 {actual} 个，建议继续保持不写）")
        return
    claimed = int(m.group(1))
    if claimed != actual:
        add(ERROR, "端点数量", f"STATUS 写『{claimed} 个端点』，实际 {actual} 个")
    else:
        add(INFO, "端点数量", f"{actual} 个，与文档一致")


# --------------------------------------------------- 4. 有提交就该有当天日志
def check_log_coverage(days: int = 45) -> None:
    raw = git("log", f"--since={days} days ago", "--pretty=format:%ad|%s", "--date=short")
    if not raw.strip():
        add(INFO, "日志覆盖", "最近没有提交")
        return

    commit_days: dict[str, int] = {}
    for line in raw.splitlines():
        if "|" not in line:
            continue
        day, subject = line.split("|", 1)
        if re.match(r"^(feat|fix|perf|refactor)", subject):
            commit_days[day] = commit_days.get(day, 0) + 1

    missing = [d for d in sorted(commit_days) if not (LOG_DIR / f"{d}.md").exists()]
    if missing:
        add(
            WARN,
            "日志覆盖",
            "这些日子有 feat/fix 提交但没有日志：" + "、".join(f"{d}({commit_days[d]}个)" for d in missing),
        )
    else:
        add(INFO, "日志覆盖", f"{len(commit_days)} 个提交日全部有日志")


# ------------------------------------------- 5. 有提交就该有交付块（最近 21 天）
def check_delivery_coverage(days: int = 21) -> None:
    raw = git("log", f"--since={days} days ago", "--pretty=format:%ad|%s", "--date=short")
    commit_days = {
        line.split("|", 1)[0]
        for line in raw.splitlines()
        if "|" in line and re.match(r"^(feat|fix|perf|refactor)", line.split("|", 1)[1])
    }
    if not commit_days:
        return

    block_days: set[str] = set()
    for p in list((ROOT / "backend" / "delivery").glob("*.md")) + [
        ROOT / "frontend" / "DELIVERY.md",
        ROOT / "eval" / "DELIVERY.md",
    ]:
        if p.exists():
            block_days |= set(re.findall(r"^##\s*(\d{4}-\d{2}-\d{2})", p.read_text(encoding="utf-8"), re.M))

    missing = sorted(d for d in commit_days if d not in block_days)
    if missing:
        add(
            WARN,
            "交付块覆盖",
            "这些日子有提交但没有交付块：" + "、".join(missing) + "（`backend/delivery/` 里补，或明确认账）",
        )
    else:
        add(INFO, "交付块覆盖", f"{len(commit_days)} 个提交日全部有交付块")


# --------------------------------------------------- 6. 速览比最新块还旧（倒挂）
def check_freshness() -> None:
    text = read("STATUS.md")
    m = re.search(r"\*\*最后更新\*\*[:：]\s*(\d{4}-\d{2}-\d{2})", text)
    if not m:
        add(ERROR, "速览新鲜度", "STATUS 速览没有『**最后更新**：YYYY-MM-DD』")
        return
    updated = m.group(1)

    block_days: list[str] = []
    for p in (ROOT / "backend" / "delivery").glob("*.md"):
        block_days += re.findall(r"^##\s*(\d{4}-\d{2}-\d{2})", p.read_text(encoding="utf-8"), re.M)

    if block_days and max(block_days) > updated:
        add(ERROR, "速览新鲜度", f"速览说 {updated}，但归档里有 {max(block_days)} 的块（速览比块旧）")
    else:
        add(INFO, "速览新鲜度", f"速览 {updated}，不早于最新交付块")


# ------------------------------------------------------- 7. 文档内相对路径存活
PATH_RE = re.compile(r"`([A-Za-z0-9_\-./]+\.(?:py|md|ts|vue|json|yml|yaml|toml|bat|css))`")


def _name_index() -> set[str]:
    """文件名索引：文档里常写短路径（`TASK.md` 其实在 eval/ 下），按 basename 认。"""
    skip = {".git", "node_modules", ".venv", "dist", "__pycache__", "storage"}
    names: set[str] = set()
    for p in ROOT.rglob("*"):
        if p.is_dir() or any(part in skip for part in p.parts):
            continue
        names.add(p.name)
    return names


def check_links() -> None:
    """只查「活文档」。历史交付块（delivery/、frontend/DELIVERY、eval/DELIVERY）记录的是当时的事实，
    里面提到的文件后来删了/改名了是正常的，改它反而篡改历史。"""
    candidates = [
        "README.md",
        "HANDOFF.md",
        "SESSION-HANDOFF.md",
        "STATUS.md",
        "docs/api.md",
    ]

    names = _name_index()
    broken: list[str] = []
    for rel in candidates:
        for m in PATH_RE.finditer(read(rel)):
            ref = m.group(1)
            if ref.startswith(("http", "..")) or "{" in ref:
                continue
            if (ROOT / ref).exists() or (ROOT / rel).parent.joinpath(ref).exists():
                continue
            if Path(ref).name in names:
                continue
            broken.append(f"{rel} → {ref}")

    if broken:
        shown = broken[:8]
        more = f" 等 {len(broken)} 处" if len(broken) > 8 else ""
        add(WARN, "文档链接", "引用了仓库里不存在的文件：" + "；".join(shown) + more)
    else:
        add(INFO, "文档链接", "全部可解析")


# --------------------------------------------- 8. 已删文件不该再被当成活文档
def check_no_dead_docs() -> None:
    dead = [
        "backend/notes_out",
        "backend/tmp/qa_exports",
    ]
    present = [d for d in dead if (ROOT / d).exists()]
    if present:
        add(INFO, "清理残留", "运行时目录存在（正常，落盘会重建）：" + "、".join(present))


def main() -> int:
    quiet = "--quiet" in sys.argv
    for fn in (
        check_api_version,
        check_test_baseline,
        check_endpoint_count,
        check_log_coverage,
        check_delivery_coverage,
        check_freshness,
        check_links,
        check_no_dead_docs,
    ):
        try:
            fn()
        except Exception as exc:  # 检查项自身崩了也要能看见
            add(ERROR, fn.__name__, f"检查本身出错：{exc}")

    order = {ERROR: 0, WARN: 1, INFO: 2}
    results.sort(key=lambda r: (order[r[0]], r[1]))
    errors = sum(1 for r in results if r[0] == ERROR)
    warns = sum(1 for r in results if r[0] == WARN)

    print("=" * 64)
    print(f"doc_lint · baleen 文档一致性    ERROR {errors} / WARN {warns}")
    print("=" * 64)
    for level, check, msg in results:
        if quiet and level == INFO:
            continue
        mark = {ERROR: "✗", WARN: "!", INFO: "·"}[level]
        print(f"{mark} [{level:<5}] {check:<12} {msg}")

    if errors == 0 and warns == 0:
        print("\n文档与代码一致。")
    elif errors:
        print("\n有 ERROR：提交前必须修掉。")
    else:
        print("\n只有 WARN：确认后可在交付块里认账。")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
