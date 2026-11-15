# -*- coding: utf-8 -*-
"""探针:导出会话 2579 的 assistant 消息(content + llm_content)成 JSON,分析数学定界符结构。"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

sql = (
    "select coalesce(json_agg(row_to_json(t))::text, '[]') from ("
    "select id, content, coalesce(llm_content,'') as llm_content "
    "from qa_messages where session_id=2579 and role='assistant' order by id) t;"
)
out = subprocess.run(
    ["docker", "exec", "baleen-db-1", "psql", "-U", "baleen", "-d", "baleen", "-t", "-A", "-c", sql],
    capture_output=True,
    text=True,
    encoding="utf-8",
)
rows = json.loads(out.stdout.strip())
tmp = os.environ["TEMP"]
for row in rows:
    for field in ("content", "llm_content"):
        text = row[field]
        dollars = text.count("$")
        if dollars == 0 and field == "llm_content":
            continue
        path = os.path.join(tmp, f"qa_{row['id']}_{field}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"--- msg {row['id']} {field}  ($: {dollars})  -> {path}")
        for i, line in enumerate(text.split("\n")):
            if "$" in line or "###" in line:
                print(f"  L{i}: {line[:200]}")
