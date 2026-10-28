"""PDF → block JSON(pymupdf)。

实现 ADR 0002 的四条硬规则:
1. 水印:丢弃行级 dir ≠ (1,0) 的斜排文本(文本流与表格单元格都按行 dir 过滤);
2. 页眉/页脚:数字掩码 + y 分桶(10pt)后跨页重复 ≥2 页即丢弃;
3. 顿号归位后处理:find_tables 单元格里被挤成独立行的标点回填到正文空格处;
4. 扫描件判定:最大页字符数 < 20 或 有文本页占比 < 10%(ADR §2.1,不用平均值)。

表格单元格文本不用 Table.extract() 的现成结果(会把压在格上的斜排水印一并收进来),
而是按 TableRow.cells 的 bbox 从 rawdict 逐字符重取:行 dir 过滤水印、(y, x) 排序、
ASCII 词距补空格。表格 bbox 内的行只在成功取到该表内容时才从文本流剔除(ADR §2.1:
find_tables 会把对齐的正文误判成表格,无条件剔除 = 正文静默丢失)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from . import blocks
from .types import PAGE_HAS_TEXT_CHARS, TEXT_PAGE_RATIO, ParseResult, ParseStatus, dist_version

ENGINE = f"pymupdf {dist_version('pymupdf')}"

_Y_BUCKET = 10  # 页眉/页脚聚类的 y 量化宽度(pt):同页眉每页 y 基本恒定
_REPEAT_MIN_PAGES = 2  # 掩码后跨页重复达到该页数即视为页眉/页脚(ADR §2.1)
# 纯标点行判定:整行只含这些字符时,把标点回填到正文空格处(顿号归位)
_PUNCT_CHARS = set("、,，。．;；:：!！?？…·-—")

_MASK_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TRAILING_PUNCT_RE = re.compile("([" + "".join(re.escape(c) for c in _PUNCT_CHARS) + "]{2,})$")


def _mask(text: str) -> str:
    """数字掩码 + 去空白:让"第 1 页 / 第 2 页"聚成同一键。"""
    return _MASK_RE.sub("#", _WS_RE.sub("", text))


def _fix_cell_punct(cell: str) -> str:
    """顿号归位(ADR §5):标点被挤出正文、堆在单元格末尾时回填到空格处。

    两种形态:
    - 多行:'明确目标 范围 规模 系统类型\\n、、、'(extract()/旧路径)——纯标点行收集;
    - 单行:'明确目标 范围 规模 系统类型、、、'(rawdict 重取后 _join_lines 已并一行)
      —— 末尾连续 ≥2 个标点的段整体回收。
    护栏:单行形态只对含 CJK 且含空格的正文生效,避免误伤英文单元格的正常标点。
    多余标点贴到末尾。
    """
    lines = cell.splitlines()
    puncts: list[str] = []
    body_lines: list[str] = []
    for ln in lines:
        stripped = ln.strip().replace("\u3000", " ")
        if stripped and set(stripped) <= _PUNCT_CHARS:
            puncts.extend(ch for ch in stripped if ch in _PUNCT_CHARS)
        else:
            body_lines.append(stripped)
    body = "".join(body_lines)
    if _HAS_CJK_RE.search(body) and " " in body:
        m = _TRAILING_PUNCT_RE.search(body)
        if m:
            puncts = list(m.group(1)) + puncts
            body = body[: m.start()]
    out: list[str] = []
    for ch in body:
        if ch == " " and puncts:
            out.append(puncts.pop(0))
        else:
            out.append(ch)
    return "".join(out) + "".join(puncts)


def _inside(point: tuple[float, float], box: tuple[float, ...]) -> bool:
    x0, y0, x1, y1 = box[0], box[1], box[2], box[3]
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


def _join_lines(lines: list[str]) -> str:
    """硬换行合并:相邻行若都是 ASCII 字母数字则补空格(英文),否则直接连(中文)。"""
    out = ""
    for ln in lines:
        if (
            out
            and out[-1].isascii()
            and out[-1].isalnum()
            and ln[:1].isascii()
            and ln[:1].isalnum()
        ):
            out += " "
        out += ln
    return out


def _cell_text(rawdict_page: dict, rect: tuple[float, float, float, float]) -> str:
    """从 rawdict 重取一个单元格的文本:非斜排字符、中心点在 bbox 内、(y, x) 排序。

    行间用 _join_lines 的 ASCII 空格规则;同行字符间距超过字号的 18% 视为词间空格。
    """
    x0, y0, x1, y1 = rect
    line_out: list[tuple[float, str]] = []
    for block in rawdict_page["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            if tuple(line.get("dir", (1.0, 0.0))) != (1.0, 0.0):
                continue  # 斜排(水印)字符不进单元格
            char_items: list[tuple[float, float, float, str, float]] = []
            for span in line["spans"]:
                size = float(span.get("size") or 10.0)
                for ch in span["chars"]:
                    b = ch["bbox"]
                    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    if x0 <= cx <= x1 and y0 <= cy <= y1 and ch["c"].strip():
                        char_items.append((cy, b[0], b[2], ch["c"], size))
            if not char_items:
                continue
            char_items.sort(key=lambda it: it[1])
            text = ""
            prev_x1: float | None = None
            for _cy, bx0, bx1, c, size in char_items:
                if prev_x1 is not None and bx0 - prev_x1 > max(1.5, size * 0.18):
                    text += " "
                text += c
                prev_x1 = bx1
            line_out.append((char_items[0][0], text))
    line_out.sort(key=lambda it: it[0])
    return _join_lines([t for _, t in line_out])


def parse_pdf(path: Path) -> ParseResult:
    doc = pymupdf.open(path)
    if doc.page_count == 0:
        return ParseResult(
            parse_status=ParseStatus.ERROR,
            message="PDF 没有任何页面,文件可能已损坏",
            meta={"engine": ENGINE},
        )
    if doc.needs_pass:
        return ParseResult(
            parse_status=ParseStatus.ERROR,
            message="PDF 已加密,请先解密后再导入",
            meta={"engine": ENGINE},
        )

    # ---- 扫描件判定:用【原始文字层】字符数,过滤之前算(ADR §2.1) ----
    per_page_chars = [len(_WS_RE.sub("", p.get_text())) for p in doc]
    has_text_pages = sum(1 for c in per_page_chars if c >= PAGE_HAS_TEXT_CHARS)
    if (
        max(per_page_chars) < PAGE_HAS_TEXT_CHARS
        or has_text_pages / doc.page_count < TEXT_PAGE_RATIO
    ):
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message="未检测到文本层(扫描件或纯图 PDF),应走 OCR 流程;G0 已拦截,不进入切块",
            meta={"engine": ENGINE, "pages": doc.page_count, "per_page_chars": per_page_chars},
        )

    # ---- 一次 rawdict 全页扫描:行过滤与单元格重取共用 ----
    pages_raw = [p.get_text("rawdict") for p in doc]
    pages_lines: list[list[tuple[int, str, bool, tuple[float, float, float, float]]]] = []
    for raw in pages_raw:
        lines = []
        for block in raw["blocks"]:
            if block["type"] != 0:  # 0=文本块;图片块不产 image 节点(仅 docx/pptx 要求)
                continue
            for line in block["lines"]:
                txt = "".join(ch["c"] for s in line["spans"] for ch in s["chars"])
                rot = tuple(line.get("dir", (1.0, 0.0))) != (1.0, 0.0)
                bbox = (line["bbox"][0], line["bbox"][1], line["bbox"][2], line["bbox"][3])
                lines.append((round(line["bbox"][1]), txt, rot, bbox))
        pages_lines.append(lines)

    # ---- 表格:结构用 find_tables,文本用 rawdict 逐格重取;只收"确实取到内容"的表 ----
    tables_by_page: dict[int, list[tuple[list[list[str]], tuple[float, ...]]]] = {}
    for pno, page in enumerate(doc):
        raw = pages_raw[pno]
        for t in page.find_tables().tables:
            row_cells = [r.cells for r in t.rows]
            if not row_cells or not row_cells[0]:
                continue
            rows = [
                [_fix_cell_punct(_cell_text(raw, cb)) if cb else "" for cb in cells]
                for cells in row_cells
            ]
            if not any(c.strip() for row in rows for c in row):
                continue
            tables_by_page.setdefault(pno, []).append((rows, t.bbox))

    # ---- 页眉/页脚:掩码 + y 桶,跨页重复 ≥2 页 ----
    pages_per_key: dict[str, set[int]] = {}
    for pno, lines in enumerate(pages_lines):
        for y, txt, rot, _ in lines:
            if rot or not txt.strip():
                continue
            pages_per_key.setdefault(f"{_mask(txt)}@{y // _Y_BUCKET}", set()).add(pno)
    repeated = {k for k, ps in pages_per_key.items() if len(ps) >= _REPEAT_MIN_PAGES}

    dropped_rot = dropped_rep = dropped_tbl = 0
    kept_by_page: list[list[tuple[int, str]]] = []
    for pno, lines in enumerate(pages_lines):
        tboxes = [b for _, b in tables_by_page.get(pno, [])]
        kept = []
        for y, txt, rot, bbox in lines:
            if rot:
                dropped_rot += 1
                continue
            if not txt.strip():
                continue
            if f"{_mask(txt)}@{y // _Y_BUCKET}" in repeated:
                dropped_rep += 1
                continue
            center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            if any(_inside(center, b) for b in tboxes):
                dropped_tbl += 1
                continue
            kept.append((y, txt))
        kept_by_page.append(kept)

    # ---- 组装 block:按 y 走,行进段落缓冲,遇到表则 flush 后插表 ----
    content: list[blocks.Block] = []

    def flush(buf: list[str]) -> None:
        if buf:
            content.append(blocks.paragraph_from_text(_join_lines(buf)))
            buf.clear()

    for pno, kept in enumerate(kept_by_page):
        page_tables = sorted(tables_by_page.get(pno, []), key=lambda tb: tb[1][1])
        buf: list[str] = []
        for y, txt in kept:
            while page_tables and page_tables[0][1][1] <= y:
                rows, _ = page_tables.pop(0)
                flush(buf)
                content.append(blocks.table_block(rows))
            buf.append(txt)
        for rows, _ in page_tables:  # 页尾剩余的表
            flush(buf)
            content.append(blocks.table_block(rows))
        flush(buf)

    warnings = []
    if dropped_rot:
        warnings.append(f"已丢弃 {dropped_rot} 行斜排文本(疑似水印)")
    if dropped_rep:
        warnings.append(
            f"已过滤 {dropped_rep} 行页眉/页脚(数字掩码后跨页重复 ≥{_REPEAT_MIN_PAGES} 页)"
        )
    if dropped_tbl:
        warnings.append(f"已剔除 {dropped_tbl} 行表格区域文本(内容保留在表格节点里)")

    chars = sum(len(t) for kept in kept_by_page for _, t in kept)
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        warnings=warnings,
        meta={
            "engine": ENGINE,
            "pages": doc.page_count,
            "chars": chars,
            "tables": sum(len(v) for v in tables_by_page.values()),
            "per_page_chars": per_page_chars,
        },
    )
