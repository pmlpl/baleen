"""CSV → 表格笔记(整篇一个/多个 table 块)。

- 编码探测与 .txt 同款:utf-8-sig → gb18030 → errors='replace'(Excel 导出的中文 CSV
  常是 GBK/GB18030,探测顺序不能反);
- 分隔符用 csv.Sniffer 探测(逗号/分号/制表符/竖线),探不出按逗号;
- 首行按表头渲染(first_row_header=True);长短不齐的行补空单元格对齐;
- 0 字节文件 ok + 空 blocks(与 .txt 同口径,不许 error 绕过 G1);
- 行数多时按 _ROWS_PER_TABLE 拆成多个 table 块——单块太大没有意义,
  切块器按块归节,拆开才能正常分块检索。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from . import blocks
from .types import ParseResult, ParseStatus

_ROWS_PER_TABLE = 200
_SAMPLE_BYTES = 64 * 1024  # Sniffer 只看开头一小段
_SNIFF_DELIMITERS = ",;\t|"


def _decode(raw: bytes) -> tuple[str, list[str]]:
    """utf-8-sig → gb18030 → replace,返回 (文本, 警告)。与 txt 直通同款顺序。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        warn = ["文件不是 UTF-8,已按 GB18030 解码"] if enc == "gb18030" else []
        return text, warn
    return raw.decode("utf-8", errors="replace"), [
        "文件编码既不是 UTF-8 也不是 GB18030,已容错解码,可能存在乱码"
    ]


def parse_csv(path: Path) -> ParseResult:
    raw = path.read_bytes()
    if not raw:
        return ParseResult(
            parse_status=ParseStatus.OK,
            meta={"engine": "csv", "rows": 0, "cols": 0, "chars": 0},
        )

    text, warnings = _decode(raw)

    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(text[:_SAMPLE_BYTES], delimiters=_SNIFF_DELIMITERS)
        delimiter = dialect.delimiter
        if delimiter != ",":
            shown = "\\t" if delimiter == "\t" else delimiter
            warnings.append(f"CSV 分隔符不是逗号,已按「{shown}」解析")
    except csv.Error:
        pass  # 探不出(Single column/空)就按逗号,csv.reader 自己也能处理

    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)  # 空行丢弃
    ]
    if not rows:
        return ParseResult(
            parse_status=ParseStatus.OK,
            warnings=warnings,
            meta={"engine": "csv", "rows": 0, "cols": 0, "chars": 0, "delimiter": delimiter},
        )

    # 长短不齐补齐到最宽行(TipTap 表格每行格数一致才渲染整齐)
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    content = [
        blocks.table_block(rows[i : i + _ROWS_PER_TABLE], first_row_header=(i == 0))
        for i in range(0, len(rows), _ROWS_PER_TABLE)
    ]
    chars = sum(len(cell) for row in rows for cell in row)
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        warnings=warnings,
        meta={
            "engine": "csv",
            "rows": len(rows),
            "cols": width,
            "chars": chars,
            "delimiter": delimiter,
        },
    )
