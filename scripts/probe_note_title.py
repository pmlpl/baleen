# -*- coding: utf-8 -*-
"""探针:扫描全库笔记标题,找出"标题 != doc 首 H1 派生值"或超长的坏标题;
对明确坏掉(len>100,通常是视频平台文案 blob)且能派生出 sane 标题的,UPDATE 修掉。

复刻 pipeline._title 的逻辑(首 heading 文本 + outline.NUMBER_PREFIX 去编号),
不 import app 模块(纯 python + docker exec psql),系统 python 即可跑。
"""
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 与 app/service/rewrite/outline.py NUMBER_PREFIX 同款(只剥行首编号)
NUMBER_PREFIX = re.compile(
    r"^\s*(?:[一二三四五六七八九十百]+[、.．]|第[一二三四五六七八九十百\d]+[章节部分]|\d+(?:\.\d+)*[、.．\s])"
)


def derive_title(doc: dict) -> str:
    for block in doc.get("content") or []:
        if block.get("type") == "heading":
            text = "".join(n.get("text", "") for n in block.get("content") or []).strip()
            if text:
                return NUMBER_PREFIX.sub("", text).strip()
    return ""  # 无 heading → 派生不出(不等于文件名,这里只看 doc)


def psql(sql: str) -> str:
    return subprocess.run(
        ["docker", "exec", "baleen-db-1", "psql", "-U", "baleen", "-d", "baleen", "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout


sql = (
    "select coalesce(json_agg(row_to_json(t))::text,'[]') from ("
    "select id, title, doc from notes where status='active' order by id) t;"
)
rows = json.loads(psql(sql).strip())

print(f"== 全库 {len(rows)} 篇 active 笔记标题扫描 ==")
mismatches = []
for r in rows:
    derived = derive_title(r["doc"] or {})
    title = r["title"] or ""
    matched = (derived != "" and title == derived)
    if not matched or len(title) > 50:
        mismatches.append((r["id"], len(title), title, derived, matched))

print(f"{'id':>6} {'len':>4} {'match':>5}  title(前40) -> derived(前40)")
for nid, ln, title, derived, matched in mismatches:
    flag = "OK" if matched else "DIFF"
    print(f"{nid:>6} {ln:>4} {flag:>5}  {title[:40]!r} -> {derived[:40]!r}")

print("\n== 自动修复(len>100 的明确坏标题,且能派生出 sane 值) ==")
fixed = 0
for nid, ln, title, derived, matched in mismatches:
    if ln > 100 and 0 < len(derived) <= 60:
        safe = derived.replace("'", "''")
        psql(f"UPDATE notes SET title='{safe}' WHERE id={nid};")
        print(f"  note {nid}: {title[:50]!r}\n        -> {derived!r}")
        fixed += 1
    elif ln > 100:
        print(f"  note {nid}: 跳过(derived 空 or 过长: {derived[:40]!r})")
print(f"\n修复 {fixed} 条。")
