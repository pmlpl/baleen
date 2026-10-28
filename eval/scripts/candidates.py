#!/usr/bin/env python3
"""baleen / eval · 阶段 1：候选特征扫描器。

本脚本**只计算可观测特征**，不做任何「算不算笔记 / 有没有价值」的判断，
不输出任何分数、等级、通过与否的结论。判定是人的事（见 eval/rubric.md）。

输出严格 12 列，定义见 eval/TASK.md D2。

用法：
    python eval/scripts/candidates.py --samples eval/samples --out eval/candidates.csv
    python eval/scripts/candidates.py --samples eval/samples --out eval/candidates.csv --resume
    python eval/scripts/candidates.py --samples eval/samples --out eval/candidates.csv \
        --random 10 --seed 42
    # 顺带生成标注表骨架（human_* 列全部留空）
    python eval/scripts/candidates.py --samples eval/samples --out eval/candidates.csv \
        --labels-out eval/labels.csv
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path
from typing import Any

from _common import build_dupe_map, load_existing, write_csv

COLUMNS = [
    "rel_path", "size_kb", "char_count", "page_count", "table_count", "image_count",
    "ocr_needed", "has_code_block", "lang_ratio_zh", "dupe_of", "longest_line", "heading_count",
]

LABEL_COLUMNS = [
    "rel_path", "filename", "ext", "char_count", "slot", "split", "human_verdict",
    "human_quality", "human_note_type", "human_value_score", "human_is_partial",
    "human_reason", "human_valued_span",
]

MD_EXTS = {".md", ".markdown"}
TEXT_EXTS = {".md", ".markdown", ".txt", ".text", ".log", ".csv", ".json"}
HTML_EXTS = {".html", ".htm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
OFFICE_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".doc"}

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FENCE_RE = re.compile(r"^\s*```", re.M)
HEADING_RE = re.compile(r"^#{1,6}\s+\S")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

MONO_FONTS = {
    "consolas",
    "courier",
    "courier new",
    "menlo",
    "monaco",
    "monospace",
    "source code pro",
    "jetbrains mono",
}

# 通用工具
def decode_bytes(raw: bytes) -> str:
    """按常见编码顺序尝试解码，永不抛异常。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")

def strip_markup(text: str) -> str:
    """极简 HTML/RTF 去标签（只为拿到近似纯文本，不做保真）。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&[a-zA-Z]+;|&#\d+;", " ", text)
    return text

def has_fence(text: str) -> bool:
    return bool(FENCE_RE.search(text))

def has_fenced_or_indented_code(text: str) -> bool:
    if has_fence(text):
        return True
    run = 0
    for line in text.splitlines():
        if line.startswith("    ") or line.startswith("\t"):
            if line.strip():
                run += 1
                if run >= 3:
                    return True
        else:
            run = 0
    return False

def count_md_headings(text: str) -> int:
    return sum(1 for line in text.splitlines() if HEADING_RE.match(line))

def count_md_tables(text: str) -> int:
    """连续的 | 行块中出现分隔行（|---|:--:|）才算一张表。"""
    count = 0
    block: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.strip().startswith("|"):
            block.append(line)
        else:
            if len(block) >= 2 and any(TABLE_SEP_RE.match(b) for b in block):
                count += 1
            block = []
    return count

# 各格式抽取：只返回事实
def empty_features() -> dict[str, Any]:
    return {"text": None, "page_count": 0, "table_count": 0,
            "image_count": 0, "heading_count": 0, "has_code_block": False}

def pdf_features(path: Path) -> dict[str, Any]:
    import fitz  # pymupdf

    feat = empty_features()
    texts: list[str] = []
    line_sizes: list[tuple[float, str]] = []
    size_weight: dict[float, int] = {}
    with fitz.open(str(path)) as doc:
        feat["page_count"] = doc.page_count
        for page in doc:
            texts.append(page.get_text("text"))
            try:
                feat["image_count"] += len(page.get_images(full=False))
            except Exception:  # noqa: BLE001 单页图片信息读不到不影响整篇
                pass
            try:
                finder = page.find_tables()
                feat["table_count"] += len(getattr(finder, "tables", finder))
            except Exception:  # noqa: BLE001 版本差异 / 无表
                pass
            try:
                for block in page.get_text("dict").get("blocks", []):
                    for line in block.get("lines", []):
                        spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                        if not spans:
                            continue
                        size = max(round(float(s["size"]), 1) for s in spans)
                        line_text = "".join(s["text"] for s in spans).strip()
                        line_sizes.append((size, line_text))
                        size_weight[size] = size_weight.get(size, 0) + len(line_text)
            except Exception:  # noqa: BLE001
                pass

    feat["text"] = "\n".join(texts)
    if line_sizes:
        body_size = max(size_weight.items(), key=lambda kv: kv[1])[0]
        feat["heading_count"] = sum(
            1 for size, t in line_sizes if size >= body_size * 1.15 and 0 < len(t) <= 80
        )
    # PDF 的 fenced / 缩进代码块不可靠，保守填 false（见 TASK.md D2）
    feat["has_code_block"] = False
    return feat

def docx_features(path: Path) -> dict[str, Any]:
    import docx

    feat = empty_features()
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    text = "\n".join(parts)

    feat["text"] = text
    feat["table_count"] = len(document.tables)
    try:
        feat["image_count"] = len(document.inline_shapes)
    except Exception:  # noqa: BLE001
        feat["image_count"] = 0
    feat["heading_count"] = sum(
        1
        for p in document.paragraphs
        if p.style is not None
        and (p.style.name or "").lower().startswith(("heading", "标题"))
    )
    mono = any(
        (run.font.name or "").lower() in MONO_FONTS
        for p in document.paragraphs
        for run in p.runs
    )
    feat["has_code_block"] = bool(mono or has_fence(text))
    return feat

def pptx_features(path: Path) -> dict[str, Any]:
    from pptx import Presentation

    feat = empty_features()
    prs = Presentation(str(path))
    parts: list[str] = []
    for slide in prs.slides:
        title = slide.shapes.title
        if title is not None and (title.text or "").strip():
            feat["heading_count"] += 1
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                feat["table_count"] += 1
                for row in shape.table.rows:
                    parts.append("\t".join(cell.text for cell in row.cells))
            if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                feat["image_count"] += 1

    feat["text"] = "\n".join(parts)
    # 幻灯片文本框不保留缩进语义，只认显式 fence
    feat["has_code_block"] = has_fence(feat["text"])
    return feat

def xlsx_features(path: Path) -> dict[str, Any]:
    import openpyxl

    feat = empty_features()
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=False)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            non_empty = False
            for row in rows:
                # 只保留有内容的单元格，避免空列制表符虚增 char_count
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if not cells:
                    continue
                non_empty = True
                parts.append("\t".join(cells))
            if non_empty:
                feat["table_count"] += 1
            feat["image_count"] += len(getattr(ws, "_images", []))
    finally:
        wb.close()

    feat["text"] = "\n".join(parts)
    # 单元格拼接出来的制表符没有代码块语义，只认显式 fence
    feat["has_code_block"] = has_fence(feat["text"])
    return feat

def legacy_doc_features(path: Path) -> dict[str, Any]:
    """旧版 .doc：能识别的（docx 改名 / HTML 导出）尽力抽，OLE2 二进制填 -1。"""
    raw = path.read_bytes()
    if raw[:2] == b"PK":
        return docx_features(path)
    head = raw[:400].lstrip().lower()
    if head.startswith(b"<") or b"<html" in head or b"mime-version" in head:
        feat = empty_features()
        feat["text"] = strip_markup(decode_bytes(raw))
        feat["has_code_block"] = has_fenced_or_indented_code(feat["text"])
        return feat
    print(f"[warn] {path.name}: 旧版二进制 .doc，无解析器 → char_count=-1", file=sys.stderr)
    return empty_features()

def image_features(path: Path) -> dict[str, Any]:
    feat = empty_features()
    feat["image_count"] = 1
    return feat

def extract_features(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        text = decode_bytes(path.read_bytes())
        feat = empty_features()
        feat["text"] = text
        if ext in MD_EXTS:
            feat["table_count"] = count_md_tables(text)
            feat["heading_count"] = count_md_headings(text)
        feat["has_code_block"] = has_fenced_or_indented_code(text)
        return feat
    if ext in HTML_EXTS:
        text = strip_markup(decode_bytes(path.read_bytes()))
        feat = empty_features()
        feat["text"] = text
        feat["has_code_block"] = has_fenced_or_indented_code(text)
        return feat
    if ext in IMAGE_EXTS:
        return image_features(path)
    if ext == ".pdf":
        return pdf_features(path)
    if ext == ".docx":
        return docx_features(path)
    if ext == ".pptx":
        return pptx_features(path)
    if ext == ".xlsx":
        return xlsx_features(path)
    if ext == ".doc":
        return legacy_doc_features(path)
    print(f"[warn] {path.name}: 未支持的后缀 {ext!r} → char_count=-1", file=sys.stderr)
    return empty_features()

# 行装配
def build_row(path: Path, root: Path) -> dict[str, str]:
    rel = path.relative_to(root).as_posix()
    try:
        feat = extract_features(path)
    except ImportError as exc:
        print(f"[warn] {rel}: 缺少解析库 {exc} → char_count=-1", file=sys.stderr)
        feat = empty_features()
    except Exception as exc:  # noqa: BLE001 单文件失败不许中断整体
        print(f"[warn] {rel}: 抽取失败 ({type(exc).__name__}: {exc}) → char_count=-1", file=sys.stderr)
        feat = empty_features()

    text = feat["text"]
    char_count = len(text) if text is not None else -1
    pages = int(feat["page_count"])
    images = int(feat["image_count"])

    cjk = len(CJK_RE.findall(text)) if text else 0
    lang_ratio = round(cjk / char_count, 3) if char_count > 0 else 0.0
    longest = max((len(line) for line in text.splitlines()), default=0) if text else 0

    return {
        "rel_path": rel,
        "size_kb": f"{path.stat().st_size / 1024:.1f}",
        "char_count": str(char_count),
        "page_count": str(pages),
        "table_count": str(int(feat["table_count"])),
        "image_count": str(images),
        "ocr_needed": "true" if char_count < 50 and (images > 0 or pages > 0) else "false",
        "has_code_block": "true" if feat["has_code_block"] else "false",
        "lang_ratio_zh": f"{lang_ratio:.3f}",
        "dupe_of": "",
        "longest_line": str(longest),
        "heading_count": str(int(feat["heading_count"])),
    }

# 目录扫描 / 去重 / 输出
def list_samples(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):  # .gitkeep 之类的占位文件不是样本
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix().lower())

def write_labels(labels_path: Path, rows: list[dict[str, str]], seed: int) -> None:
    # 按 seed 洗牌后切 60/40，保证比例稳定可复现（新增样本会重排，阶段 1 可接受）
    order = [row["rel_path"] for row in rows]
    random.Random(seed).shuffle(order)
    n_dev = round(len(order) * 0.6)
    dev_set = set(order[:n_dev])

    label_rows: list[dict[str, str]] = []
    for row in rows:
        rel = row["rel_path"]
        label_rows.append(
            {
                "rel_path": rel,
                "filename": rel.rsplit("/", 1)[-1],
                "ext": Path(rel).suffix.lower().lstrip("."),
                "char_count": row["char_count"],
                "slot": "",
                "split": "dev" if rel in dev_set else "test",
                # 第 7–13 列一律留空，由人标注
            }
        )
    write_csv(labels_path, LABEL_COLUMNS, label_rows)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描 eval/samples/，输出 12 列特征表")
    parser.add_argument("--samples", default="eval/samples", help="样本根目录")
    parser.add_argument("--out", default="eval/candidates.csv", help="输出 CSV 路径")
    parser.add_argument("--resume", action="store_true", help="跳过输出文件里已有的 rel_path")
    parser.add_argument("--random", type=int, default=None, help="只保留随机抽取的 N 个文件")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    parser.add_argument("--labels-out", default=None, help="同时生成 labels.csv 骨架")
    args = parser.parse_args(argv)

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[error] 样本目录不存在: {root}", file=sys.stderr)
        return 2

    files = list_samples(root)
    if not files:
        print(f"[error] {root} 下没有文件", file=sys.stderr)
        return 2

    # 去重信息需要对全部文件算哈希，不受 --random 影响
    dupe_map = build_dupe_map(files, root)

    selected = files
    if args.random is not None and args.random < len(files):
        rng = random.Random(args.seed)
        selected = sorted(rng.sample(files, args.random), key=lambda p: p.relative_to(root).as_posix().lower())

    existing = load_existing(Path(args.out)) if args.resume else {}
    rows: list[dict[str, str]] = []
    for path in selected:
        rel = path.relative_to(root).as_posix()
        if rel in existing:
            row = {key: existing[rel].get(key, "") for key in COLUMNS}
            row["dupe_of"] = ""  # 重新计算
            rows.append(row)
        else:
            rows.append(build_row(path, root))

    rows.sort(key=lambda r: r["rel_path"].lower())
    for row in rows:
        row["dupe_of"] = dupe_map.get(row["rel_path"], "")
    write_csv(Path(args.out), COLUMNS, rows)
    print(f"[ok] {len(rows)} 行 → {args.out}")

    if args.labels_out:
        write_labels(Path(args.labels_out), rows, args.seed)
        print(f"[ok] {len(rows)} 行 → {args.labels_out}（human_* 列留空）")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
