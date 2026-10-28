"""切块器(ADR 0004):block JSON → sections[] + chunks[](父块/子块)。

纯函数,不碰数据库、不加载模型。token 计数由调用方注入 `token_counter`
(生产用 app.service.embed.count_tokens,测试用假计数器),保证 HARD_TOKEN_LIMIT
是用 bge tokenizer 实算的,而不是字数估算(ADR §6)。

三层结构:note → section(父块,不 embed)→ chunk(子块 250–400 字,embed)。
五类难处理单元:表格按行+重复表头、代码按函数边界、无标题长文按句子累积、
目录页 skipped_embed、图片块文本极少不 embed。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .constants import (
    MAX_CHARS,
    MIN_CHARS,
    MODEL_MAX_TOKENS,
    SENTENCE_BOUNDARIES,
    TARGET_CHARS,
    TOC_AVG_LINE_CHARS,
    TOC_SHORT_LINE_CHARS,
    TOC_SHORT_LINE_RATIO,
)

TokenCounter = Callable[[str], int]

_SENT_SPLIT = re.compile(rf"(?<=[{re.escape(SENTENCE_BOUNDARIES)}])")
_TOC_PATTERN = re.compile(
    r"(第[\d一二三四五六七八九十百千]+[章节条部分]|[0-9]+(?:\.[0-9]+)+|…{2,}|^\s*\d+\s*$)"
)
# 代码函数/类边界(粗粒度,够用)
_CODE_BOUNDARY = re.compile(
    r"^\s*(?:async\s+def|def|class|function|const|public|private|protected|static)\b"
)


@dataclass
class Chunk:
    text: str
    heading_path: str | None
    ord: int
    is_parent: bool
    token_count: int
    meta: dict = field(default_factory=dict)
    section_idx: int = 0


@dataclass
class Section:
    heading_path: str | None
    text: str  # 父块全文(整节,给 LLM 当上下文)
    meta: dict = field(default_factory=dict)
    chunk_indices: list[int] = field(default_factory=list)


@dataclass
class ChunkResult:
    chunks: list[Chunk]
    sections: list[Section]
    metrics: dict


def _default_token_counter(text: str) -> int:
    """无注入时的兜底:按非空白字符数估(1 汉字≈1 token)。生产请注入真 tokenizer。"""
    return len(re.sub(r"\s+", "", text))


def _block_text(block: dict) -> str:
    """单块的纯文本(父块上下文用)。"""
    t = block.get("type")
    if t in ("paragraph", "heading"):
        return "".join(n.get("text", "") for n in block.get("content") or [])
    if t == "codeBlock":
        return "".join(n.get("text", "") for n in block.get("content") or [])
    if t == "image":
        return f"[图片] {block.get('attrs', {}).get('alt', '')}".strip()
    if t == "table":
        return _table_to_text(block)
    if t in ("bulletList", "orderedList"):
        return "\n".join(_block_text(item) for item in block.get("content") or [])
    if t == "listItem":
        return "\n".join(_block_text(b) for b in block.get("content") or [])
    if t == "blockquote":
        return "\n".join(_block_text(b) for b in block.get("content") or [])
    return ""


def _table_rows(block: dict) -> tuple[list[str], list[list[str]]]:
    """返回 (表头单元格, 数据行单元格列表)。无 thead 时表头为空。"""
    header: list[str] = []
    body: list[list[str]] = []
    for row in block.get("content") or []:
        cells = [
            "".join(
                n.get("text", "")
                for para in (c.get("content") or [])
                for n in (para.get("content") or [])
            )
            for c in row.get("content") or []
        ]
        if not header and any(c.get("type") == "tableHeader" for c in row.get("content") or []):
            header = cells
        else:
            body.append(cells)
    return header, body


def _table_to_text(block: dict) -> str:
    header, body = _table_rows(block)
    lines = []
    if header:
        lines.append(" | ".join(header))
    for row in body:
        lines.append(" | ".join(row))
    return "\n".join(lines)


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(text) if s.strip()]


def _split_text_to_chunks(
    text: str,
    heading_path: str | None,
    token_counter: TokenCounter,
    section_idx: int,
    ord_start: int,
    *,
    force_single: bool = False,
    skipped_embed: bool = False,
    meta_extra: dict | None = None,
) -> tuple[list[Chunk], list[str]]:
    """把一段文本按句子累积成若干子块;返回 (chunks, warnings)。

    force_single: 不切(目录/小表格/短代码),整段一个子块。
    skipped_embed: 该子块不进 embeddings(目录、低文本图片)。
    """
    warnings: list[str] = []
    if not text.strip():
        return [], warnings
    meta_base: dict = {"skipped_embed": skipped_embed}
    if meta_extra:
        meta_base.update(meta_extra)

    if force_single:
        chunks = [
            Chunk(
                text=text,
                heading_path=heading_path,
                ord=ord_start,
                is_parent=False,
                token_count=token_counter(text),
                meta=meta_base,
                section_idx=section_idx,
            )
        ]
        return chunks, warnings

    sentences = _split_sentences(text)
    chunks: list[Chunk] = []
    buf = ""
    buf_chars = 0
    ord_n = ord_start

    def flush() -> None:
        nonlocal buf, buf_chars, ord_n
        if buf.strip():
            chunks.append(
                Chunk(
                    text=buf,
                    heading_path=heading_path,
                    ord=ord_n,
                    is_parent=False,
                    token_count=token_counter(buf),
                    meta=dict(meta_base),
                    section_idx=section_idx,
                )
            )
            ord_n += 1
        buf, buf_chars = "", 0

    for s in sentences:
        s_chars = len(re.sub(r"\s+", "", s))
        if s_chars > MAX_CHARS:
            # 单句超长:整句保留,宁可超长也不毁语义(ADR §3)
            flush()
            chunks.append(
                Chunk(
                    text=s,
                    heading_path=heading_path,
                    ord=ord_n,
                    is_parent=False,
                    token_count=token_counter(s),
                    meta=dict(meta_base),
                    section_idx=section_idx,
                )
            )
            ord_n += 1
            warnings.append(f"单句超 {MAX_CHARS} 字({s_chars} 字),整句保留未切")
            continue
        if buf_chars and buf_chars + s_chars > MAX_CHARS:
            flush()
        buf += s
        buf_chars += s_chars
        if buf_chars >= TARGET_CHARS:
            flush()
    flush()

    # 末块过短并到前一块(除非会顶过 MAX:宁可留短块也不超限)
    if len(chunks) >= 2:
        last_chars = len(re.sub(r"\s+", "", chunks[-1].text))
        prev_chars = len(re.sub(r"\s+", "", chunks[-2].text))
        if last_chars < MIN_CHARS and prev_chars + last_chars <= MAX_CHARS:
            chunks[-2].text += chunks[-1].text
            chunks[-2].token_count = token_counter(chunks[-2].text)
            chunks.pop()
    return chunks, warnings


def _chunk_table(
    block: dict,
    heading_path: str | None,
    token_counter: TokenCounter,
    section_idx: int,
    ord_start: int,
) -> tuple[list[Chunk], list[str]]:
    header, body = _table_rows(block)
    warnings: list[str] = []
    if not body:
        return [], warnings
    header_text = " | ".join(header) if header else ""
    total_chars = sum(len(re.sub(r"\s+", "", " | ".join(r))) for r in body) + len(header_text)
    # 小表格整张一个子块
    if total_chars <= MAX_CHARS or len(body) <= 1:
        text = _table_to_text(block)
        chunks, w = _split_text_to_chunks(
            text,
            heading_path,
            token_counter,
            section_idx,
            ord_start,
            force_single=True,
            meta_extra={"block_type": "table"},
        )
        return chunks, warnings + w
    # 大表格:按行切,每块重复表头
    chunks: list[Chunk] = []
    ord_n = ord_start
    buf_rows: list[list[str]] = []
    buf_chars = len(header_text)
    part = 0

    def flush() -> None:
        nonlocal buf_rows, buf_chars, ord_n, part
        if not buf_rows:
            return
        part += 1
        lines = [f"[表格({part})]" + (f" 表头：{header_text}" if header_text else "")]
        lines += [" | ".join(r) for r in buf_rows]
        text = "\n".join(lines)
        chunks.append(
            Chunk(
                text=text,
                heading_path=heading_path,
                ord=ord_n,
                is_parent=False,
                token_count=token_counter(text),
                meta={"block_type": "table", "table_part": part},
                section_idx=section_idx,
            )
        )
        ord_n += 1
        buf_rows, buf_chars = [], len(header_text)

    for row in body:
        row_chars = len(re.sub(r"\s+", "", " | ".join(row)))
        if buf_rows and buf_chars + row_chars > MAX_CHARS:
            flush()
        buf_rows.append(row)
        buf_chars += row_chars
    flush()
    return chunks, warnings


def _chunk_code(
    block: dict,
    heading_path: str | None,
    token_counter: TokenCounter,
    section_idx: int,
    ord_start: int,
) -> tuple[list[Chunk], list[str]]:
    code = "".join(n.get("text", "") for n in block.get("content") or [])
    language = (block.get("attrs") or {}).get("language", "")
    warnings: list[str] = []
    if not code.strip():
        return [], warnings
    ctx = f"# {heading_path or '代码'}" + (f" ({language})" if language else "")

    lines = code.splitlines()
    groups: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if _CODE_BOUNDARY.match(ln) and cur:
            groups.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        groups.append(cur)
    # 无函数边界 → 按空行分组
    if len(groups) <= 1:
        groups = []
        cur = []
        for ln in lines:
            if ln.strip() == "" and cur:
                groups.append(cur)
                cur = []
            else:
                cur.append(ln)
        if cur:
            groups.append(cur)

    chunks: list[Chunk] = []
    ord_n = ord_start
    for g in groups:
        body = "\n".join(g)
        text = f"{ctx}\n{body}"
        tc = token_counter(text)
        # 代码不受 MAX_CHARS 严格约束,但 HARD_TOKEN_LIMIT 仍生效;超了给 warning 整段保留
        if tc > MODEL_MAX_TOKENS:
            warnings.append(f"代码块 token={tc} 超 {MODEL_MAX_TOKENS},整段保留未切")
        chunks.append(
            Chunk(
                text=text,
                heading_path=heading_path,
                ord=ord_n,
                is_parent=False,
                token_count=tc,
                meta={"block_type": "code", "language": language},
                section_idx=section_idx,
            )
        )
        ord_n += 1
    return chunks, warnings


def _looks_like_toc(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        return False
    short = sum(1 for ln in lines if len(ln.strip()) < TOC_SHORT_LINE_CHARS)
    ratio = short / len(lines)
    avg = sum(len(ln.strip()) for ln in lines) / len(lines)
    pattern_hits = sum(1 for ln in lines if _TOC_PATTERN.search(ln.strip()))
    hits = 0
    if pattern_hits >= len(lines) * 0.4:
        hits += 1
    if avg < TOC_AVG_LINE_CHARS:
        hits += 1
    if ratio > TOC_SHORT_LINE_RATIO:
        hits += 1
    return hits >= 2


def chunk(
    doc: dict,
    *,
    token_counter: TokenCounter | None = None,
) -> ChunkResult:
    """block JSON → sections + chunks。token_counter 不注入则用字数兜底(仅测试用)。"""
    if token_counter is None:
        token_counter = _default_token_counter

    blocks = doc.get("content") or []
    sections: list[Section] = []
    flat: list[Chunk] = []
    warnings: list[str] = []

    heading_stack: list[str] = []
    cur_section_blocks: list[dict] = []
    cur_pending_text = ""  # 当前段落在累积的正文(表格/代码/标题会打断)
    cur_chunk_indices: list[int] = []  # 本 section 已 emit 的 flat 下标,section_idx 待回填

    def breadcrumb() -> str | None:
        return " > ".join(heading_stack) if heading_stack else None

    def _emit(cs: list[Chunk]) -> None:
        for c in cs:
            c.section_idx = -1  # 占位,flush_section 回填真实列表下标
            flat.append(c)
            cur_chunk_indices.append(len(flat) - 1)

    def flush_pending(ord_start: int, toc: bool) -> int:
        nonlocal cur_pending_text
        if not cur_pending_text.strip():
            return ord_start
        if toc:
            cs, w = _split_text_to_chunks(
                cur_pending_text,
                breadcrumb(),
                token_counter,
                -1,
                ord_start,
                force_single=True,
                skipped_embed=True,
                meta_extra={"block_type": "toc"},
            )
        else:
            cs, w = _split_text_to_chunks(
                cur_pending_text,
                breadcrumb(),
                token_counter,
                -1,
                ord_start,
                meta_extra={"block_type": "text"},
            )
        warnings.extend(w)
        _emit(cs)
        return ord_start + len(cs)

    def flush_section(ord_start: int) -> int:
        nonlocal cur_section_blocks, cur_pending_text, cur_chunk_indices
        # 先冲掉残余正文,顺便判目录
        is_toc = not any(
            b.get("type") in ("table", "codeBlock", "image") for b in cur_section_blocks
        ) and _looks_like_toc("\n".join(_block_text(b) for b in cur_section_blocks))
        ord_n = flush_pending(ord_start, is_toc)
        cur_pending_text = ""
        sec_text = "\n".join(
            _block_text(b) for b in cur_section_blocks if b.get("type") != "heading"
        ).strip()
        # 有子块或父块文本才建 section;子块存在时即便父文本空也给它们一个家,避免孤儿
        if not cur_chunk_indices and not sec_text:
            cur_section_blocks = []
            cur_chunk_indices = []
            return ord_n
        sec_list_idx = len(sections)
        if sec_text:
            parent = Chunk(
                text=sec_text,
                heading_path=breadcrumb(),
                ord=ord_n,
                is_parent=True,
                token_count=token_counter(sec_text),
                meta={"block_type": "section", "is_toc": is_toc},
                section_idx=sec_list_idx,
            )
            flat.append(parent)
            cur_chunk_indices.append(len(flat) - 1)
            ord_n += 1
        # 回填本 section 所有块(子块 + 父块)的 section_idx
        for ci in cur_chunk_indices:
            flat[ci].section_idx = sec_list_idx
        sections.append(
            Section(
                heading_path=breadcrumb(),
                text=sec_text,
                meta={"is_toc": is_toc},
                chunk_indices=list(cur_chunk_indices),
            )
        )
        cur_section_blocks = []
        cur_chunk_indices = []
        return ord_n

    # 表格/代码/图片:打断正文,先 flush_pending(非 toc),再产专项子块
    def handle_special(block: dict, ord_start: int) -> int:
        nonlocal cur_pending_text
        ord_n = flush_pending(ord_start, toc=False)
        cur_pending_text = ""
        t = block.get("type")
        if t == "table":
            cs, w = _chunk_table(block, breadcrumb(), token_counter, -1, ord_n)
        elif t == "codeBlock":
            cs, w = _chunk_code(block, breadcrumb(), token_counter, -1, ord_n)
        elif t == "image":
            alt = (block.get("attrs") or {}).get("alt", "[图片]")
            text = f"[图片] {alt}".strip()
            cs = [
                Chunk(
                    text=text,
                    heading_path=breadcrumb(),
                    ord=ord_n,
                    is_parent=False,
                    token_count=token_counter(text),
                    meta={"block_type": "image", "skipped_embed": True},
                    section_idx=-1,
                )
            ]
            w = []
        else:
            return ord_n
        warnings.extend(w)
        _emit(cs)
        return ord_n + len(cs)

    ord_n = 0
    for block in blocks:
        bt = block.get("type")
        if bt == "heading":
            ord_n = flush_section(ord_n)
            level = min((block.get("attrs") or {}).get("level", 1), 6)
            title = "".join(n.get("text", "") for n in block.get("content") or [])
            heading_stack = heading_stack[: level - 1] + ([title] if title else [])
            cur_section_blocks = [block]  # 新父块以标题打头
            continue
        if bt in ("table", "codeBlock", "image"):
            cur_section_blocks.append(block)
            ord_n = handle_special(block, ord_n)
            continue
        # 普通段落 / 列表 / 引用 → 累积正文
        cur_section_blocks.append(block)
        piece = _block_text(block)
        if piece.strip():
            cur_pending_text += piece + "\n"

    ord_n = flush_section(ord_n)

    # 重排 ord 连续(chunk_indices 已在 flush_section 回填)
    for i, c in enumerate(flat):
        c.ord = i

    metrics = _metrics(flat, token_counter, warnings)
    return ChunkResult(chunks=flat, sections=sections, metrics=metrics)


def _metrics(chunks: list[Chunk], token_counter: TokenCounter, warnings: list[str]) -> dict:
    children = [c for c in chunks if not c.is_parent]
    over_limit = sum(1 for c in children if c.token_count > 480)
    truncated = sum(1 for c in children if c.token_count > MODEL_MAX_TOKENS)
    in_range = sum(
        1
        for c in children
        if MIN_CHARS <= len(re.sub(r"\s+", "", c.text)) <= MAX_CHARS
        and not c.meta.get("skipped_embed")
    )
    embeddable = [c for c in children if not c.meta.get("skipped_embed")]
    ratio = (in_range / len(embeddable)) if embeddable else 1.0
    if ratio < 0.6:
        warnings.append(f"chunks_in_range 仅 {ratio:.0%},低于 60%(ADR §6)")
    return {
        "chunks_total": len(chunks),
        "children_total": len(children),
        "chunks_over_limit": over_limit,
        "chunks_truncated": truncated,
        "chunks_in_range_ratio": round(ratio, 3),
        "warnings": warnings,
    }
