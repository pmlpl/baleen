"""Markdown → block JSON(markdown-it-py)。

token 流直转:块级 token 用栈组装,行内 token 递归处理 marks
(bold/italic/link/strikethrough/code)。文件按 UTF-8 读(仅 .txt 走编码探测链,ADR §2.1)。
"""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from . import blocks
from .types import ParseResult, ParseStatus, dist_version

ENGINE = f"markdown-it-py {dist_version('markdown-it-py')}"


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
        elif tok.type == "image":  # 自闭合 image token:alt 在 children
            alt = "".join(c.content for c in tok.children or [] if c.type == "text")
            if alt.strip():
                holders[-1].append(blocks.text_node(alt.strip()))
        # softbreak/hardbreak:中文场景直接吞掉(段落内不产换行节点)
    return nodes


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


def parse_md_text(text: str) -> tuple[list[blocks.Block], int]:
    """markdown 字符串 → block JSON 数组(笔记编辑保存走这里;文件入口 parse_md 同款转换)。"""
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    return _tokens_to_blocks(md.parse(text))


def parse_md(path: Path) -> ParseResult:
    raw = path.read_text(encoding="utf-8")
    content, chars = parse_md_text(raw)
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": content},
        meta={"engine": ENGINE, "chars": chars},
    )
