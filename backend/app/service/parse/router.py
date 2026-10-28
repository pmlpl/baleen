"""G0 格式路由:扩展名 → 解析器,统一产出 ParseResult。

- .pdf / .docx / .md / .pptx → 对应解析器;.txt 纯文本直通(ADR §2.1);
- .doc 报「请转成 .docx」;.xlsx/.xls 暂不支持;纯图片 → no_text_expected(该走 OCR,
  不是格式错误);其他扩展名 → unsupported_format;
- 认识的格式解析时抛异常(损坏/加密)→ error,不让异常逃出 G0。
"""

from __future__ import annotations

from pathlib import Path

from . import blocks
from .docx_parser import parse_docx
from .md_parser import parse_md
from .pdf_parser import parse_pdf
from .pptx_parser import parse_pptx
from .types import ParseResult, ParseStatus


def parse_txt(path: Path) -> ParseResult:
    """纯文本直通(ADR §2.1):编码探测 utf-8-sig → gb18030 → errors='replace'。

    0 字节文件:ok + 空 blocks + chars=0,不许返回 error(否则空文件绕过 G1 价值闸)。
    """
    raw = path.read_bytes()
    if not raw:
        return ParseResult(
            parse_status=ParseStatus.OK,
            meta={"engine": "txt-direct", "chars": 0},
        )

    warnings: list[str] = []
    text: str | None = None
    used = ""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
        used = "utf-8(replace)"
        warnings.append("文件编码既不是 UTF-8 也不是 GB18030,已容错解码,可能存在乱码")
    elif used == "gb18030":
        warnings.append("文件不是 UTF-8,已按 GB18030 解码")

    content = [blocks.paragraph_from_text(ln) for ln in text.splitlines() if ln.strip()]
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        warnings=warnings,
        meta={"engine": "txt-direct", "chars": len(text)},
    )


PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".md": parse_md,
    ".markdown": parse_md,
    ".pptx": parse_pptx,
    ".txt": parse_txt,
}

# 纯图片:没有文本层,该走 OCR,不归 unsupported(ADR §2.1)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}

UNSUPPORTED_MESSAGES = {
    ".doc": "不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .docx 后再导入",
    ".xlsx": "暂不支持 .xlsx 表格簿,请另存为 CSV,或把内容整理进 .md/.txt 后导入",
    ".xls": "暂不支持 .xls 表格簿,请另存为文本形式(CSV/.md/.txt)后导入",
}


def parse_file(path: Path | str) -> ParseResult:
    """G0 唯一入口:按扩展名路由;任何解析异常都收敛为 ParseResult。"""
    p = Path(path)
    ext = p.suffix.lower()

    if ext in IMAGE_EXTS:
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message="纯图片没有文本层,G0 不解析其内容;该文件应排入 OCR 队列(后续流程),暂不入库",
            meta={"engine": "none", "chars": 0},
        )
    if ext in UNSUPPORTED_MESSAGES:
        return ParseResult(
            parse_status=ParseStatus.UNSUPPORTED_FORMAT,
            message=UNSUPPORTED_MESSAGES[ext],
            meta={"engine": "none", "chars": 0},
        )
    parser = PARSERS.get(ext)
    if parser is None:
        return ParseResult(
            parse_status=ParseStatus.UNSUPPORTED_FORMAT,
            message=f"暂不支持的格式:{ext or '(无扩展名)'}",
            meta={"engine": "none", "chars": 0},
        )
    try:
        return parser(p)
    except Exception as exc:  # G0 不允许异常外逃,统一收敛为 error
        return ParseResult(
            parse_status=ParseStatus.ERROR,
            message=f"解析失败:{type(exc).__name__}: {exc}"[:300],
            meta={"engine": "none", "chars": 0},
        )
