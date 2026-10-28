"""TipTap 兼容 block JSON 的构造辅助(AGENTS.md §5 决策3)。

只做"纯字典组装",不引第三方类型;节点形状对齐 TipTap/ProseMirror:
paragraph/heading 的 content 是 inline(text)节点数组,tableCell 的 content 是段落数组。
"""

from __future__ import annotations

type Inline = dict
type Block = dict


def empty_doc() -> dict:
    return {"type": "doc", "content": []}


def text_node(text: str, marks: list[dict] | None = None) -> Inline:
    node: Inline = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def paragraph(inlines: list[Inline]) -> Block:
    return {"type": "paragraph", "content": inlines}


def paragraph_from_text(text: str) -> Block:
    return paragraph([text_node(text)])


def heading(level: int, text: str) -> Block:
    return {"type": "heading", "attrs": {"level": level}, "content": [text_node(text)]}


def code_block(text: str, language: str = "") -> Block:
    return {"type": "codeBlock", "attrs": {"language": language}, "content": [text_node(text)]}


def blockquote(children: list[Block]) -> Block:
    return {"type": "blockquote", "content": children}


def bullet_list(items: list[list[Block]]) -> Block:
    return {"type": "bulletList", "content": [{"type": "listItem", "content": it} for it in items]}


def ordered_list(items: list[list[Block]], start: int = 1) -> Block:
    node: Block = {
        "type": "orderedList",
        "content": [{"type": "listItem", "content": it} for it in items],
    }
    if start != 1:
        node["attrs"] = {"start": start}
    return node


def horizontal_rule() -> Block:
    return {"type": "horizontalRule"}


def image_node(index: int, alt: str = "[图片]", **extra_attrs) -> Block:
    """图片占位节点(ADR §2.1):docx/pptx/md 里的图不允许静默丢失。"""
    attrs = {"alt": alt, "index": index, **extra_attrs}
    return {"type": "image", "attrs": attrs}


def table_block(rows: list[list[str]], *, first_row_header: bool = False) -> Block:
    """字符串矩阵 → TipTap table;单元格内的换行拆成多个段落。"""

    def cell(text: str, kind: str) -> Block:
        paras = [paragraph_from_text(ln) for ln in text.splitlines() if ln.strip()]
        return {"type": kind, "content": paras or [paragraph_from_text("")]}  # 空格也要占位

    content = []
    for r, row in enumerate(rows):
        kind = "tableHeader" if first_row_header and r == 0 else "tableCell"
        content.append({"type": "tableRow", "content": [cell(c, kind) for c in row]})
    return {"type": "table", "content": content}


def flatten_text(doc: dict) -> str:
    """把 block JSON 里的所有 text 拼成一串(测试与日志用),块间以 \\n 分隔。"""
    parts: list[str] = []

    def walk(node: dict) -> None:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        for child in node.get("content") or []:
            walk(child)

    walk(doc)
    return "\n".join(p for p in parts if p)
