"""DOCX → block JSON(python-docx)。

- iter_inner_content() 按 document order 保序输出段落/表格(AGENTS.md §5 决策3);
- 页眉/页脚走 section.header/footer 独立通道,本解析器不读 → 正文天然无页眉噪声;
- 图片不静默丢:段落里的 w:drawing 产出 image 占位节点(ADR §2.1)。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from . import blocks
from .types import ParseResult, ParseStatus, dist_version

ENGINE = f"python-docx {dist_version('python-docx')}"

_HEADING_RE = re.compile(r"^(?:Heading|标题)\s*(\d)$", re.IGNORECASE)


def _heading_level(p: Paragraph) -> int | None:
    name = p.style.name if p.style is not None else ""
    m = _HEADING_RE.match(name.strip())
    if m:
        return min(int(m.group(1)), 6)
    if name.strip() == "Title":
        return 1
    return None


def _run_inlines(runs) -> tuple[list[blocks.Inline], int]:
    """run 序列 → inline 节点;返回 (inlines, 文本字符数)。"""
    inlines: list[blocks.Inline] = []
    chars = 0
    for run in runs:
        text = run.text
        if not text:
            continue
        marks = []
        if run.bold:
            marks.append({"type": "bold"})
        if run.italic:
            marks.append({"type": "italic"})
        if run.underline:
            marks.append({"type": "underline"})
        inlines.append(blocks.text_node(text, marks or None))
        chars += len(text)
    return inlines, chars


def _paragraph_block(p: Paragraph) -> tuple[blocks.Block | None, list[blocks.Block], int]:
    """段落 → (主块, 尾随 image 节点, 字符数)。超链接文本经 iter_inner_content 保留。"""
    inlines: list[blocks.Inline] = []
    chars = 0
    for child in p.iter_inner_content():
        if isinstance(child, Run):
            nodes, n = _run_inlines([child])
            inlines.extend(nodes)
            chars += n
        else:  # Hyperlink:文本不丢,整段文本带 link mark
            href = child.address or ""
            nodes, n = _run_inlines(child.runs)
            for node in nodes:
                if href:
                    node["marks"] = (node.get("marks") or []) + [
                        {"type": "link", "attrs": {"href": href}}
                    ]
            inlines.extend(nodes)
            chars += n

    images = [blocks.image_node(-1) for _ in p._p.findall(".//" + qn("w:drawing"))] + [
        blocks.image_node(-1) for _ in p._p.findall(".//" + qn("w:pict"))
    ]

    level = _heading_level(p)
    if level is not None:
        text = "".join(t.get("text", "") for t in inlines)
        main: blocks.Block | None = blocks.heading(level, text) if text else None
    elif inlines:
        main = blocks.paragraph(inlines)
    else:
        main = None
    return main, images, chars


def parse_docx(path: Path) -> ParseResult:
    doc: DocumentObject = Document(path)
    content: list[blocks.Block] = []
    chars = 0
    n_tables = 0
    image_index = 0

    for item in doc.iter_inner_content():
        if isinstance(item, Paragraph):
            main, images, n = _paragraph_block(item)
            if main is not None:
                content.append(main)
                chars += n
            for img in images:
                img["attrs"]["index"] = image_index
                content.append(img)
                image_index += 1
        elif isinstance(item, Table):
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            content.append(blocks.table_block(rows))
            chars += sum(len(c) for row in rows for c in row)
            n_tables += 1

    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        meta={
            "engine": ENGINE,
            "chars": chars,
            "tables": n_tables,
            "images": image_index,
        },
    )
