"""笔记用户自编辑:doc → markdown 序列化 + 编辑保存(换 doc + 重建切块 + 重嵌)。

「重建」配方与线 R 回填共享(delete_by_note → chunk → bulk_insert → embed),
但**不跑 LLM**——用户改的是显式内容,结构就是用户的意志,不做二次加工;
回填的幂等闸(parse_meta.headings_rewritten)保持原值,后续回填也不会覆写用户 edits。

已知取舍(诚实记录):
- 序列化不做全文 markdown 转义,只挡最危险的行首元字符(# > - 数字.);段落含
  成对 ** 等符号时往返可能变样式——编辑器给的是 markdown 源,用户自己可见可改。
- **图片块(2026-11-02 v16):改为位置标记往返**。序列化时每张图写成
  ``![alt](baleen-image:N)``(N = 文档顺序),保存时按 N 把编辑前的原 image 节点
  原样放回原位——图片字节本来就不在 doc 里(只有 alt/index),所以「重关联」
  关联的是节点本身,不是像素。用户可以把标记行挪位置(图跟着挪)、删掉(图也删),
  这是显式意图,不算丢数据。API 层不再对含图笔记返回 400。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note
from app.repo.repos import Repos
from app.service.chunk import chunk
from app.service.chunk.chunker import TokenCounter
from app.service.embed import EMBEDDING_MODEL, count_tokens
from app.service.parse.md_parser import IMAGE_SCHEME, ImageResolver, parse_md_text

# 与 pipeline/backfill 同款:(list[str]) → 矩阵
EmbedFunc = Callable[[list[str]], Any]

# 9 是 markdown-it table 插件的上限,与 G0 导入口径一致
_MAX_TABLE_COLS = 9


def doc_has_image_blocks(doc: dict | None) -> bool:
    def walk(node: dict) -> bool:
        if node.get("type") == "image":
            return True
        return any(walk(child) for child in node.get("content") or [])

    return doc is not None and walk(doc)


def _escape_leading(text: str) -> str:
    """挡行首元字符:防止普通段落往返变成标题/列表/引用。"""
    return re.sub(r"^(#{1,6} |>|-|\+|\*|\d+[.)])", r"\\\1", text)


def _inline_to_md(node: dict) -> str:
    text = node.get("text", "")
    marks = node.get("marks") or []
    types = [m.get("type") for m in marks]
    if "code" in types:
        return f"`{text}`"
    if "bold" in types:
        text = f"**{text}**"
    if "italic" in types:
        text = f"*{text}*"
    if "strike" in types:
        text = f"~~{text}~~"
    link = next((m for m in marks if m.get("type") == "link"), None)
    if link is not None:
        href = (link.get("attrs") or {}).get("href", "")
        text = f"[{text}]({href})"
    return text


def _inlines_to_md(inlines: list[dict] | None, *, escape_lead: bool = False) -> str:
    parts = [_inline_to_md(n) for n in inlines or []]
    text = "".join(parts)
    return _escape_leading(text) if escape_lead else text


def _cell_text(cell: dict) -> str:
    paras = cell.get("content") or []
    text = " ".join(_inlines_to_md(p.get("content")) for p in paras).strip()
    return text.replace("|", "\\|")


def _table_to_md(block: dict) -> str:
    rows = block.get("content") or []
    lines: list[str] = []
    for r, row in enumerate(rows):
        cells = row.get("content") or []
        lines.append("| " + " | ".join(_cell_text(c) for c in cells[:_MAX_TABLE_COLS]) + " |")
        if r == 0 and any(c.get("type") == "tableHeader" for c in cells):
            lines.append("| " + " | ".join(["---"] * len(cells[:_MAX_TABLE_COLS])) + " |")
    return "\n".join(lines)


def _list_to_md(
    block: dict, *, ordered: bool, indent: str = "", image_counter: list[int] | None = None
) -> list[str]:
    """列表序列化;嵌套列表缩进两格(与前端渲染器同款的一层口径)。

    ``image_counter`` 透传:列表项里的 image 块也要参与位置标记编号
    (v16 边界测试抓到的真 bug——不传的话列表内的图会被静默丢弃)。
    """
    lines: list[str] = []
    start = int((block.get("attrs") or {}).get("start", 1) or 1)
    for i, item in enumerate(block.get("content") or []):
        prefix = f"{start + i}. " if ordered else "- "
        pieces: list[str] = []
        for child in item.get("content") or []:
            t = child.get("type")
            if t == "paragraph":
                pieces.append(_inlines_to_md(child.get("content")))
            elif t in ("bulletList", "orderedList"):
                nested = _list_to_md(
                    child, ordered=t == "orderedList", indent=indent + "  ",
                    image_counter=image_counter,
                )
                pieces.append("\n" + "\n".join(nested) if nested else "")
            elif t == "codeBlock":
                code = "".join(n.get("text", "") for n in child.get("content") or [])
                pieces.append("\n" + indent + "  " + code.replace("\n", "\n" + indent + "  "))
            elif t == "image" and image_counter is not None:
                # 图片块在列表项里:产出标记行,与块级图共用同一计数器。
                # 标记行用「item 内容缩进」的延续行写法(不能带空行——空行会终结列表,
                # 图就掉到顶层了;也不能顶格——顶格同样终结列表)
                alt = (child.get("attrs") or {}).get("alt", "") or "图片"
                index = image_counter[0]
                image_counter[0] += 1
                pieces.append(indent + "  " + f"![{alt}]({IMAGE_SCHEME}{index})")
        body = pieces[0] if pieces and pieces[0] != "" else ""
        rest = [p for p in pieces[1:] if p]
        lines.append(indent + prefix + body)
        lines.extend(rest)
    return lines


def _iter_images(nodes: list[dict]) -> list[dict]:
    """文档顺序收集 image 节点(含容器内嵌套)。"""
    found: list[dict] = []
    for node in nodes:
        if node.get("type") == "image":
            found.append(node)
        found.extend(_iter_images(node.get("content") or []))
    return found


def _block_to_md(block: dict, image_counter: list[int]) -> str:
    """单块 → markdown;``image_counter`` 是跨块共享的可变计数器(图片按文档顺序编号)。"""
    t = block.get("type")
    if t == "heading":
        level = min(int((block.get("attrs") or {}).get("level", 1) or 1), 6)
        return "#" * level + " " + _inlines_to_md(block.get("content"))
    if t == "paragraph":
        return _inlines_to_md(block.get("content"), escape_lead=True)
    if t == "codeBlock":
        code = "".join(n.get("text", "") for n in block.get("content") or [])
        lang = (block.get("attrs") or {}).get("language", "") or ""
        fence = "```"
        while fence in code:  # 代码里已有围栏时加长,保证不提前闭合
            fence += "`"
        return f"{fence}{lang}\n{code}\n{fence}"
    if t == "table":
        return _table_to_md(block)
    if t == "blockquote":
        inner = [_block_to_md(b, image_counter) for b in block.get("content") or []]
        joined = "\n\n".join(x for x in inner if x)
        return "\n".join("> " + ln if ln else ">" for ln in joined.splitlines())
    if t == "bulletList":
        return "\n".join(_list_to_md(block, ordered=False, image_counter=image_counter))
    if t == "orderedList":
        return "\n".join(_list_to_md(block, ordered=True, image_counter=image_counter))
    if t == "horizontalRule":
        return "---"
    if t == "image":
        # 位置标记:保存时按 N 把原 image 节点放回原位(图字节不进 doc,见模块 docstring)
        alt = (block.get("attrs") or {}).get("alt", "") or "图片"
        index = image_counter[0]
        image_counter[0] += 1
        return f"![{alt}]({IMAGE_SCHEME}{index})"
    return ""


def doc_to_markdown(doc: dict | None) -> str:
    """block JSON → markdown(编辑器工作稿)。空 doc 返回空串。"""
    if not doc:
        return ""
    counter = [0]
    parts = [_block_to_md(b, counter) for b in doc.get("content") or []]
    return "\n\n".join(p for p in parts if p.strip())


def _image_restorer(old_doc: dict | None) -> ImageResolver:
    """``baleen-image:N`` → 编辑前原 image 节点的 attrs(按文档顺序取第 N 张)。"""
    old_content = (old_doc or {}).get("content") or []
    originals = [img.get("attrs") or {} for img in _iter_images(old_content)]

    def resolve(src: str, alt: str) -> dict | None:
        if not src.startswith(IMAGE_SCHEME):
            return {"src": src} if src and not src.startswith("data:") else None
        try:
            index = int(src[len(IMAGE_SCHEME) :])
        except ValueError:
            return None
        attrs = originals[index] if 0 <= index < len(originals) else None
        if attrs is None:  # 用户手写了越界编号:退化成 alt 文本,别崩
            return None
        restored = dict(attrs)
        if alt:
            restored["alt"] = alt  # 允许用户改替代文字
        return restored

    return resolve


@dataclass
class NoteEditOutcome:
    note_id: int
    title_changed: bool
    content_changed: bool
    chunks_parents: int
    chunks_children: int
    embeddings: int


async def apply_note_edit(
    session: AsyncSession,
    repos: Repos,
    note: Note,
    *,
    title: str | None = None,
    markdown: str | None = None,
    embed_func: EmbedFunc,
    token_counter: TokenCounter | None = None,
) -> NoteEditOutcome:
    """应用编辑并重建检索索引;同内容重复保存是 no-op(不重嵌)。调用方负责 404/400。"""
    outcome = NoteEditOutcome(
        note_id=note.id,
        title_changed=False,
        content_changed=False,
        chunks_parents=0,
        chunks_children=0,
        embeddings=0,
    )

    if title is not None and title != note.title:
        await repos.notes.update(note.id, title=title)
        outcome.title_changed = True

    if markdown is not None:
        # 图片标记按编辑前的原 doc 重新关联;解析不了的退化成 alt 文本
        doc_blocks, _chars = parse_md_text(markdown, image_resolver=_image_restorer(note.doc))
        new_doc: dict = {"type": "doc", "content": doc_blocks}
        if new_doc != (note.doc or {"type": "doc", "content": []}):
            # 换 doc + 重建切块 + 重嵌同一事务:任何一步炸了整体回滚,笔记保持原样
            await repos.notes.update(note.id, doc=new_doc)
            await repos.chunks.delete_by_note(note.id)
            result = chunk(new_doc, token_counter=token_counter or count_tokens)
            inserted = await repos.chunks.bulk_insert(note.id, result)
            todo = [
                (row.id, c.text) for c, row in inserted if not (row.meta or {}).get("skipped_embed")
            ]
            vectors = embed_func([text for _, text in todo])
            rows = [
                (cid, EMBEDDING_MODEL, len(vec), [float(x) for x in vec])
                for (cid, _), vec in zip(todo, vectors, strict=True)
            ]
            if rows:
                await repos.embeddings.bulk_insert(rows)
            outcome.content_changed = True
            outcome.chunks_parents = sum(1 for c in result.chunks if c.is_parent)
            outcome.chunks_children = sum(1 for c in result.chunks if not c.is_parent)
            outcome.embeddings = len(rows)

    source = await repos.sources.get(note.source_id) if note.source_id else None
    if source is not None:
        meta = dict(source.parse_meta or {})
        meta["user_edited"] = True
        source.parse_meta = meta

    await session.commit()
    return outcome


__all__ = [
    "EmbedFunc",
    "NoteEditOutcome",
    "apply_note_edit",
    "doc_has_image_blocks",
    "doc_to_markdown",
]
