"""PPTX → block JSON(python-pptx)。

- 每页:标题占位符 → heading(level=2);文本框 → paragraph;图形框表格 → table;
- 图片不静默丢(ADR §2.1):PICTURE 形状产出 image 占位节点;
- 演讲者备注不取(演示者材料,不属于笔记正文);
- 组合形状(GROUP)递归展开。
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.shapes.base import BaseShape

from . import blocks
from .types import ParseResult, ParseStatus, dist_version

ENGINE = f"python-pptx {dist_version('python-pptx')}"


class _Counters:
    def __init__(self) -> None:
        self.images = 0
        self.tables = 0
        self.chars = 0


def _shape_text(shape: BaseShape) -> list[blocks.Block]:
    out: list[blocks.Block] = []
    if not shape.has_text_frame:
        return out
    for p in shape.text_frame.paragraphs:
        text = "".join(run.text for run in p.runs) or p.text
        if text.strip():
            out.append(blocks.paragraph_from_text(text))
    return out


def _walk_shapes(shapes, content: list[blocks.Block], st: _Counters, skip_shape_id=None) -> None:
    for shape in shapes:
        if skip_shape_id is not None and shape.shape_id == skip_shape_id:
            continue  # 标题已作为 heading 输出,避免重复
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _walk_shapes(shape.shapes, content, st, skip_shape_id)
            continue
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            content.append(blocks.image_node(st.images))
            st.images += 1
            continue
        if shape.has_table:
            rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
            content.append(blocks.table_block(rows))
            st.tables += 1
            st.chars += sum(len(c) for row in rows for c in row)
            continue
        for node in _shape_text(shape):
            content.append(node)
            st.chars += len(node["content"][0]["text"])


def parse_pptx(path: Path) -> ParseResult:
    prs = Presentation(path)
    content: list[blocks.Block] = []
    st = _Counters()
    for slide in prs.slides:
        title = None
        try:
            if slide.shapes.title is not None:
                title = slide.shapes.title.text.strip()
        except Exception:  # 无标题占位符的版式
            title = None
        if title:
            content.append(blocks.heading(2, title))
            st.chars += len(title)
        _walk_shapes(
            slide.shapes, content, st,
            slide.shapes.title.shape_id if title else None,
        )

    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        meta={
            "engine": ENGINE,
            "slides": len(prs.slides),
            "chars": st.chars,
            "tables": st.tables,
            "images": st.images,
        },
    )
