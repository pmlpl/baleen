# -*- coding: utf-8 -*-
"""探针:用后端真实 doc_to_markdown 把笔记 4983 序列化成 md,存 %TEMP%/note4983.md。
embed 模块只被 notes_edit 引了常量,stub 掉避免加载 torch。
"""
import json
import os
import sys
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

sys.modules.setdefault("app.service.embed", mock.MagicMock())
from app.service.notes_edit import doc_to_markdown  # noqa: E402

src = os.path.join(os.environ["TEMP"], "note4983.json")
doc = json.load(open(src, encoding="utf-8"))
md = doc_to_markdown(doc)

out = os.path.join(os.environ["TEMP"], "note4983.md")
with open(out, "w", encoding="utf-8") as f:
    f.write(md)
print("md chars:", len(md))
print("raw $ count:", md.count("$"))
print("$$ count:", md.count("$$"))
