"""Markdown → block JSON(markdown-it-py)。

token 流直转:块级 token 用栈组装,行内 token 递归处理 marks
(bold/italic/link/strikethrough/code)。文件按 UTF-8 读(仅 .txt 走编码探测链,ADR §2.1)。

**图片(2026-11-02 v16)**:`![alt](src)` 不再展平成 alt 文本(ADR §2.1「图片不静默丢」)。
解析期先产出行内标记节点 ``imageMarker``,再由 ``split_image_markers`` 把段落拆开、
在图片原位插入块级 ``image`` 节点;``image_resolver`` 决定这个 src 怎么落地:
- md 文件导入 → 相对路径解析 + 存字节,attrs 带 ``media``(内容寻址 key)
- 笔记编辑往返 → ``baleen-image:N`` 还原成编辑前的原 image 节点
- 解析不了(文件不存在/外链) → 退回 alt 文本并记 warning,不丢正文
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from . import blocks
from .types import ParseResult, ParseStatus, dist_version

ENGINE = f"markdown-it-py {dist_version('markdown-it-py')}"

# 图片 src → image 节点 attrs;返回 None 表示「这个 src 落不了地」,退回 alt 文本
ImageResolver = Callable[[str, str], dict | None]

# 笔记编辑往返用的伪 scheme:``![alt](baleen-image:0)`` 指回原 doc 里的第 0 张图
IMAGE_SCHEME = "baleen-image:"


def _mark_for_open(tok: Token) -> dict | None:
    if tok.type == "strong_open":
        return {"type": "bold"}
    if tok.type == "em_open":
        return {"type": "italic"}
    if tok.type == "s_open":
        return {"type": "strike"}
    if tok.type == "link_open":
        return {"type": "link", "attrs": {"href": tok.attrGet("href") or ""}}
    return None  # 其他 open token 不产生 mark


def _render_inlines(children: list[Token]) -> list[blocks.Inline]:
    """行内 token 序列 → inline 节点(open/close 配对递归)。"""
    nodes: list[blocks.Inline] = []
    stack: list[dict] = []
    holders: list[list[blocks.Inline]] = [nodes]
    for tok in children:
        if tok.type == "text":
            holders[-1].append(blocks.text_node(tok.content, list(stack[-1]) if stack else None))
        elif tok.type == "code_inline":
            marks = list(stack[-1]) if stack else []
            holders[-1].append(blocks.text_node(tok.content, marks + [{"type": "code"}]))
        elif tok.type.endswith("_open"):
            mark = _mark_for_open(tok)
            if mark is not None:
                stack.append((stack[-1] if stack else []) + [mark])
            else:  # image 等:展平为 alt 文本
                alt = tok.content or ""
                if alt:
                    holders[-1].append(blocks.text_node(alt, list(stack[-1]) if stack else None))
        elif tok.type.endswith("_close"):
            if stack:
                stack.pop()
        elif tok.type == "image":  # 自闭合 image token:alt 在 children,src 在 attrs
            alt = "".join(c.content for c in tok.children or [] if c.type == "text").strip()
            holders[-1].append(
                {"type": "imageMarker", "attrs": {"src": tok.attrGet("src") or "", "alt": alt}}
            )
        # softbreak/hardbreak:中文场景直接吞掉(段落内不产换行节点)
    return nodes


def _split_paragraph_images(node: dict, resolve: ImageResolver, warnings: list[str]) -> list[dict]:
    """含 imageMarker 的段落 → 按图片位置拆成 [段落, 图, 段落, …]。

    TipTap 的 image 是块级节点,不能塞在 paragraph 的行内 content 里,
    所以拆分是必须的;拆完段落顺序与原文档一致,图片停在原来的位置。
    """
    inlines: list[dict] = node.get("content") or []
    if not any(n.get("type") == "imageMarker" for n in inlines):
        return [node]

    out: list[dict] = []
    buf: list[dict] = []

    def flush() -> None:
        if buf:
            out.append({"type": "paragraph", "content": list(buf)})
            buf.clear()

    for inline in inlines:
        if inline.get("type") != "imageMarker":
            buf.append(inline)
            continue
        attrs = inline.get("attrs") or {}
        src = (attrs.get("src") or "").strip()
        alt = (attrs.get("alt") or "").strip()
        resolved = resolve(src, alt)
        if resolved is None:
            # 落不了地:保留 alt 文本(与旧行为一致的空安全兜底),并明确记一条
            if alt:
                buf.append(blocks.text_node(alt))
            warnings.append(f"图片未能导入,已保留替代文字:{alt or src or '(无)'}")
            continue
        flush()
        out.append({"type": "image", "attrs": {"alt": alt, **resolved}})
    flush()
    return out or [{"type": "paragraph", "content": []}]


def _tokens_to_blocks(tokens: list[Token]) -> tuple[list[blocks.Block], int]:
    root: blocks.Block = {"type": "doc", "content": []}
    stack: list[blocks.Block] = [root]
    chars = 0

    def top() -> blocks.Block:
        return stack[-1]

    def push(node: blocks.Block) -> None:
        top()["content"].append(node)
        stack.append(node)

    def pop() -> blocks.Block:
        return stack.pop()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        t = tok.type
        if t == "heading_open":
            push({"type": "heading", "attrs": {"level": int(tok.tag[1])}, "content": []})
        elif t == "heading_close":
            pop()
        elif t == "paragraph_open":
            push({"type": "paragraph", "content": []})
        elif t == "paragraph_close":
            node = pop()
            chars += sum(len(c.get("text", "")) for c in node.get("content") or [])
        elif t == "inline":
            node = top()
            if tok.children:
                inlines = _render_inlines(tok.children)
            else:
                inlines = [blocks.text_node(tok.content)]
            if node["type"] in ("tableCell", "tableHeader"):
                node.setdefault("_inlines", []).extend(inlines)
            else:
                node["content"] = inlines
        elif t == "fence":
            lang = tok.info.strip().split()[0] if tok.info.strip() else ""
            node = blocks.code_block(tok.content.rstrip("\n"), lang)
            push(node)
            stack.pop()
            chars += len(tok.content)
        elif t == "hr":
            push(blocks.horizontal_rule())
            stack.pop()
        elif t == "blockquote_open":
            push({"type": "blockquote", "content": []})
        elif t == "blockquote_close":
            pop()
        elif t in ("bullet_list_open", "ordered_list_open"):
            kind = "bulletList" if t == "bullet_list_open" else "orderedList"
            node: blocks.Block = {"type": kind, "content": []}
            if kind == "orderedList":
                start = tok.attrGet("start")
                if start and start != "1":
                    node["attrs"] = {"start": int(start)}
            push(node)
        elif t in ("bullet_list_close", "ordered_list_close"):
            node = pop()
            chars += sum(
                len(c.get("text", ""))
                for item in node["content"]
                for blk in item.get("content", [])
                for c in blk.get("content", [])
            )
        elif t == "list_item_open":
            push({"type": "listItem", "content": []})
        elif t == "list_item_close":
            pop()
        elif t == "table_open":
            push({"type": "table", "content": []})
        elif t == "table_close":
            node = pop()
            chars += sum(
                len(c.get("text", ""))
                for row in node["content"]
                for cell in row.get("content", [])
                for para in cell.get("content", [])
                for c in para.get("content", [])
            )
        elif t in ("thead_open", "tbody_open"):
            pass
        elif t in ("thead_close", "tbody_close"):
            pass
        elif t == "tr_open":
            push({"type": "tableRow", "content": []})
        elif t == "tr_close":
            pop()
        elif t in ("th_open", "td_open"):
            kind = "tableHeader" if t == "th_open" else "tableCell"
            push({"type": kind, "content": []})
        elif t in ("th_close", "td_close"):
            cell = pop()
            inlines = cell.pop("_inlines", [])
            cell["content"] = (
                [blocks.paragraph(inlines)] if inlines else [blocks.paragraph_from_text("")]
            )
        i += 1

    return root["content"], chars


# 递归容器:段落可能嵌在这些块里,拆分要一路递归进去。
# bulletList/orderedList 不能漏——漏了的话列表项里的图升不了块级(v17 边界测试抓到)
_NESTED_CONTAINERS = ("blockquote", "listItem", "bulletList", "orderedList")


def split_image_markers(
    nodes: list[dict], resolve: ImageResolver, warnings: list[str]
) -> list[dict]:
    """递归把含 imageMarker 的段落拆开,图片升成块级节点(容器内也递归)。"""
    out: list[dict] = []
    for node in nodes:
        if node.get("type") == "paragraph":
            out.extend(_split_paragraph_images(node, resolve, warnings))
            continue
        if node.get("type") in _NESTED_CONTAINERS and node.get("content"):
            node = {**node, "content": split_image_markers(node["content"], resolve, warnings)}
        out.append(node)
    return out


def _default_resolver(src: str, _alt: str) -> dict | None:
    """没有媒体后端时的口径:保留 src 引用(不下载、不展平),图片节点仍然成立。"""
    if not src or src.startswith("data:"):
        return None
    return {"src": src}


def parse_md_text(
    text: str,
    *,
    image_resolver: ImageResolver | None = None,
    warnings: list[str] | None = None,
) -> tuple[list[blocks.Block], int]:
    """markdown 字符串 → block JSON 数组(笔记编辑保存走这里;文件入口 parse_md 同款转换)。"""
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    content, chars = _tokens_to_blocks(md.parse(text))
    warn_bucket = warnings if warnings is not None else []
    content = split_image_markers(content, image_resolver or _default_resolver, warn_bucket)
    return content, chars


def parse_md(
    path: Path,
    *,
    media_sink: Callable[[bytes, str], str] | None = None,
) -> ParseResult:
    """md 文件 → blocks;引用到的本地图片可以经 ``media_sink`` 存字节。

    ``media_sink(bytes, alt) -> key``:由调用方决定存哪(流水线存内容寻址目录)。
    没给 sink 时图片只保留 src 引用(不丢节点,也不复制文件)。
    """
    raw = path.read_text(encoding="utf-8")
    warnings: list[str] = []
    base = path.parent

    def resolve(src: str, alt: str) -> dict | None:
        if src.startswith(IMAGE_SCHEME) or src.startswith("data:"):
            return None
        if "://" in src:  # 外链:不联网抓取(G0 不做网络 IO),只记引用
            return {"src": src}
        target = (base / src).resolve()
        if not target.is_file():
            return None
        try:
            data = target.read_bytes()  # noqa: ASYNC240 —— 纯本地小文件同步读,与其它解析器一致
        except OSError:
            return None
        if media_sink is None:
            return {"src": src, "filename": target.name}
        return {"media": media_sink(data, alt), "src": src, "filename": target.name}

    content, chars = parse_md_text(raw, image_resolver=resolve, warnings=warnings)
    images = _count_images(content)
    meta: dict = {"engine": ENGINE, "chars": chars}
    if images:
        meta["images"] = images
    if media_sink is not None and images:
        warnings.append(f"已随笔记导入 {images} 张本地图片(按内容寻址存库,笔记详情页可查看)")
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        warnings=warnings,
        meta=meta,
    )


def _count_images(nodes: list[dict]) -> int:
    total = 0
    for node in nodes:
        if node.get("type") == "image":
            total += 1
        total += _count_images(node.get("content") or [])
    return total
