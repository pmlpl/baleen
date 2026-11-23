"""HTML → TipTap blocks(标准库 html.parser,零第三方依赖)。

附件/导入的 HTML 多是「网页另存/简历导出」产物:样式与内嵌 base64 图占据绝大体积
(实测一份 18 万字符简历,90% 是一张 data URI 照片)。解析策略:

- 剥 script/style/head/svg 等非正文节点;
- 块级标签(p/div/hN/li/tr/td…)处断行,h1-h6 产 heading 块,其余成段落;
- ``<img src="data:image/...;base64,...">`` 解码字节经 ``media_sink`` 回调
  (与 md 本地图同语义:导入路径落库、轻量解析路径回传前端走视觉通道);
- 外链/相对路径图片不联网抓取(与 md 外链同语义),退化成替代文字 + warning;
- 编码探测与 parse_txt 同款:utf-8-sig → gb18030 → replace。
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path

from . import blocks
from .types import ParseResult, ParseStatus

# 块级标签:开始/结束时断行(td/th 拆开让表格单元格各占一行,比拼成一行可读)
_BREAK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "caption", "dd", "div", "dl", "dt",
        "fieldset", "figure", "figcaption", "footer", "form", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# 整段跳过的非正文节点(head 里的 style/title 也顺带被 head 覆盖)。
# ⚠️ 不含 void 标签(meta/link/br 等):它们没有 endtag,进了这个集合会让 skip_depth
# 只增不减,把后续 body 全吞掉(实测一份带 2 个 meta 的简历导致正文全空)
_SKIP_TAGS = frozenset(
    {"canvas", "embed", "head", "iframe", "noscript", "object", "script", "style", "svg", "template", "title"}
)
# data URI 图片:HTML 简历的头像/照片几乎全是这个形态
_DATA_URI_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)\s*$")


def _decode(raw: bytes) -> tuple[str, list[str]]:
    """编码探测与 parse_txt 同款:utf-8-sig → gb18030 → replace。"""
    warnings: list[str] = []
    try:
        return raw.decode("utf-8-sig"), warnings
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("gb18030")
        warnings.append("文件不是 UTF-8,已按 GB18030 解码")
        return text, warnings
    except UnicodeDecodeError:
        pass
    warnings.append("文件编码既不是 UTF-8 也不是 GB18030,已容错解码,可能存在乱码")
    return raw.decode("utf-8", errors="replace"), warnings


class _Extractor(HTMLParser):
    """把 HTML 拆成「标题/段落行 + data URI 图片」的顺序条目流。

    行缓冲:文本累积到当前行,遇块级标签开/闭或 br 时落一行;
    落行时全空白的缓冲直接丢弃(标签间的排版缩进不产空段落)。
    """

    def __init__(self, sink: Callable[[bytes, str], str] | None) -> None:
        super().__init__(convert_charrefs=True)
        self._sink = sink
        # 顺序条目:{"kind":"text","level":N,"text":...} | {"kind":"image","index":i,"alt":...,"media":key}
        self.items: list[dict] = []
        self.image_count = 0
        self.dropped_imgs = 0
        self.oversized_imgs = 0  # sink 因超限拒收的图片数(与解析失败分开计数便于提示)
        self._buf: list[str] = []
        self._skip_depth = 0
        self._heading = 0  # 当前处于 hN 内,落行时作 heading level

    # ---- 行缓冲 ----

    def _flush_line(self) -> None:
        text = "".join(self._buf).strip()
        self._buf.clear()
        if text:
            self.items.append({"kind": "text", "level": self._heading, "text": text})

    def close(self) -> None:
        super().close()
        # 兜底:尾部文本若无块级标签结尾,close 后也落行(否则最后一段会丢)
        self._flush_line()

    # ---- 事件 ----

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br":
            self._flush_line()
            return
        if tag == "img":
            self._handle_img(dict(attrs))
            return
        if tag in _BREAK_TAGS:
            self._flush_line()
            if tag in _HEADING_TAGS:
                self._heading = int(tag[1])

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _BREAK_TAGS:
            self._flush_line()
            if tag in _HEADING_TAGS:
                self._heading = 0

    def handle_data(self, data):
        if self._skip_depth:
            return
        # HTML 源码里的换行/缩进是排版空白,统一当空格(pre 内换行丢失,可接受)
        self._buf.append(" ".join(data.split()))

    # ---- 图片 ----

    def _handle_img(self, attrs: dict[str, str | None]) -> None:
        src = (attrs.get("src") or "").strip()
        alt = (attrs.get("alt") or "").strip() or "[图片]"
        if not src:
            return
        match = _DATA_URI_RE.match(src)
        self._flush_line()  # 图片无论收下与否都打断当前行,保持正文顺序
        if match:
            mime, b64 = match.group(1), match.group(2)
            try:
                raw = base64.b64decode(re.sub(r"\s+", "", b64))
            except (binascii.Error, ValueError):
                self.dropped_imgs += 1
                return
            if self._sink is not None:
                try:
                    key = self._sink(raw, alt)
                except ValueError:
                    # sink 拒收(如超过单张大小上限)→ 与「无 sink」同口径退 alt,不炸 G0
                    self.dropped_imgs += 1
                    self.oversized_imgs += 1
                    self.items.append({"kind": "text", "level": 0, "text": alt})
                    return
                self.items.append(
                    {
                        "kind": "image",
                        "index": self.image_count,
                        "alt": alt,
                        "media": key,
                    }
                )
                self.image_count += 1
                return
            # 无 sink(直接调用/导入未传):与 md data URI 同口径,退化成替代文字
        self.dropped_imgs += 1
        self.items.append({"kind": "text", "level": 0, "text": alt})


def parse_html(path: Path, *, media_sink: Callable[[bytes, str], str] | None = None) -> ParseResult:
    """HTML 文件 → ParseResult;HTMLParser 容错极强,坏结构也尽量提取。"""
    p = Path(path)
    raw = p.read_bytes()
    text, warnings = _decode(raw)
    if not text.strip():
        return ParseResult(parse_status=ParseStatus.OK, meta={"engine": "html-stdlib", "chars": 0})

    extractor = _Extractor(media_sink)
    try:
        extractor.feed(text)
        extractor.close()
    except Exception:  # noqa: BLE001 —— 已提取的部分照用,不让坏 HTML 炸掉 G0
        warnings.append("HTML 结构异常,已按可解析部分提取")

    content: list[dict] = []
    for item in extractor.items:
        if item["kind"] == "image":
            content.append(blocks.image_node(item["index"], alt=item["alt"], media=item["media"]))
        elif item["level"]:
            content.append(blocks.heading(item["level"], item["text"]))
        else:
            content.append(blocks.paragraph_from_text(item["text"]))
    if extractor.oversized_imgs:
        warnings.append(
            f"{extractor.oversized_imgs} 张图片超过单张大小上限,未导入(已保留替代文字)"
        )
    if extractor.dropped_imgs - extractor.oversized_imgs:
        warnings.append(
            f"{extractor.dropped_imgs - extractor.oversized_imgs} 张外链/本地图片未导入,"
            "已保留替代文字"
        )

    doc = {"type": "doc", "content": content}
    flat = blocks.flatten_text(doc)
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks=doc,
        warnings=warnings,
        meta={"engine": "html-stdlib", "chars": len(flat), "images": extractor.image_count},
    )
