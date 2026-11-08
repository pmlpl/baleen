#!/usr/bin/env python3
"""doc_backfill —— 从 git log 生成「待填交付块」骨架。

解决什么：交付时漏写块，事后补的成本高到没人愿意补，于是永远欠账。
这里把「能机械提取的事实」（日期、提交、改了哪些文件）先填好，
人只剩三格要写：为什么做 / 怎么验的 / 已知限制。

用法：
    python scripts/doc_backfill.py --since 2026-11-01
    python scripts/doc_backfill.py --since 2026-11-01 --until 2026-11-08

输出：backend/delivery/pending.md（草稿）
- 标题故意用 `###`，**不会**被 doc_lint 当成已有交付块，所以欠账提示不会因此消失；
- 填完把块挪进 `backend/delivery/2026-11-*.md`，标题改成 `## YYYY-MM-DD ...`，这个草稿就该删。
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "delivery" / "pending.md"

PREFIX_TYPES = r"(feat|fix|perf|refactor)"


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return out.stdout


def files_of(sha: str) -> list[str]:
    stat = git("show", "--name-status", "--format=", sha)
    rows = []
    for line in stat.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append(f"{parts[0]} {parts[1]}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    args = ap.parse_args()

    cmd = ["log", f"--since={args.since}"]
    if args.until:
        cmd.append(f"--until={args.until}")
    cmd += ["--pretty=format:%h|%ad|%s", "--date=short", "--reverse"]

    raw = git(*cmd)
    commits = []
    for line in raw.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and re.match(PREFIX_TYPES, parts[2]):
            commits.append(parts)

    if not commits:
        print("这段时间没有 feat/fix/perf/refactor 提交。")
        return

    blocks: list[str] = []
    for sha, day, subject in commits:
        files = files_of(sha)
        listed = "\n".join(f"- `{f}`" for f in files[:12]) or "- （虚提交/仅改文档）"
        tail = f"\n- …共 {len(files)} 个文件" if len(files) > 12 else ""
        blocks.append(
            f"""### 待填 · {day} · `{sha}` · {subject}

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

{listed}{tail}

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__
"""
        )

    header = f"""# 待填交付块（{args.since} 起）

> 由 `scripts/doc_backfill.py` 从 `git log` 生成，只包含**能机械提取的事实**。
> 三格 `__待填__` 必须人写——写不出来的，说明这次交付本身没想清。
>
> 填完：把块挪进 `backend/delivery/` 对应月份文件，标题 `### 待填 · ` 改成 `## `，
> 然后删掉本文件。**在本文件存在期间，doc_lint 的「交付块覆盖」WARN 不会消失**，这是故意的。

---

"""
    OUT.write_text(header + "\n---\n\n".join(blocks), encoding="utf-8")
    print(f"生成 {OUT.relative_to(ROOT)}：{len(commits)} 个待填块")
    for _, day, subject in commits:
        print(f"  {day}  {subject[:60]}")


if __name__ == "__main__":
    main()
