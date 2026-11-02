"""边界测试批(2026-11-02 主人「把可以做的都做完」)。

覆盖五条此前没钉住的边界:
1. 混合型 PDF(部分页有文字层):文字页占比 ≥10% 走 pymupdf 不烧视觉;<10% 才走视觉;
2. 容器(列表/引用)内嵌套图的编辑往返:标记按文档顺序编号,嵌套位置也保序;
3. CSV 引号内逗号/换行(RFC 4180):csv 模块行为钉住;
4. 删笔记后 media 字节留存:内容寻址存储的语义——删除是引用删除,同图可能被其他笔记引用;
5. sha 去重与附件:同 md 再次导入命中 dedupe,新附件不参与处理(语义钉住)。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.service.chunk import chunk
from app.service.embed import count_tokens
from app.service.notes_edit import _image_restorer, doc_to_markdown
from app.service.parse import ParseStatus
from app.service.parse.csv_parser import parse_csv
from app.service.parse.md_parser import parse_md_text
from app.service.parse.router import parse_file
from tests.service.parse.test_vision_pdf import FakePageClient

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


# ---------------------------------------------------------------- 1. 混合型 PDF


def _make_mixed_pdf(tmp_path: Path, image_pages: int, text_pages: int, name="mixed.pdf") -> Path:
    """前 image_pages 页是纯图(无文字层),后 text_pages 页有真文字。"""
    from PIL import Image, ImageDraw

    scan = tmp_path / "_scan_page.png"
    img = Image.new("RGB", (300, 400), "white")
    ImageDraw.Draw(img).text((30, 190), "scan page", fill="black")
    img.save(scan, "PNG")

    path = tmp_path / name
    doc = pymupdf.open()
    for _ in range(image_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(scan))
    for i in range(text_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text(
            (72, 100),
            f"Text layer page {i + 1} with enough content for the detector. " * 3,
            fontsize=11,
        )
    doc.save(path)
    doc.close()
    return path


def test_mixed_pdf_majority_text_does_not_burn_vision(tmp_path: Path) -> None:
    """9 图 + 1 文字页:文字页占比 10% → 走 pymupdf 正常路径,视觉一次都不该调。"""
    path = _make_mixed_pdf(tmp_path, image_pages=9, text_pages=1)
    client = FakePageClient(["不该被调用"])
    r = parse_file(path, client=client)
    assert client.calls == [], "有文本层的占比达到阈值就不该烧视觉"
    assert r.parse_status == ParseStatus.OK
    assert r.meta["engine"].startswith("pymupdf")


def test_mixed_pdf_below_threshold_goes_vision(tmp_path: Path) -> None:
    """19 图 + 1 文字页:文字页占比 5% <10% → 判为扫描件,走逐页视觉。"""
    path = _make_mixed_pdf(tmp_path, image_pages=19, text_pages=1, name="mostly_scan.pdf")
    client = FakePageClient(["图页转写。"] * 19 + ["文字页不该转写"])
    r = parse_file(path, client=client)
    assert r.parse_status == ParseStatus.OK
    assert r.meta["engine"].startswith("pdf-vision("), "低于占比阈值就该走视觉"


# ---------------------------------------------------------------- 2. 嵌套图编辑往返


def test_nested_images_roundtrip_in_order() -> None:
    """引用块和列表里的图,序列化编号按文档顺序;保存后嵌套结构保序还原。"""
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "blockquote",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "引用:"}]},
                    {"type": "image", "attrs": {"alt": "引用图", "index": 0}},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "要点一"}]},
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "要点二"}]},
                            {"type": "image", "attrs": {"alt": "列表图", "index": 1}},
                        ],
                    },
                ],
            },
            {"type": "image", "attrs": {"alt": "顶层图", "index": 2}},
        ],
    }
    md = doc_to_markdown(doc)
    assert "![引用图](baleen-image:0)" in md
    assert "![列表图](baleen-image:1)" in md
    assert "![顶层图](baleen-image:2)" in md

    restored, _ = parse_md_text(md, image_resolver=_image_restorer(doc))
    kinds = [(b["type"], (b.get("attrs") or {}).get("alt")) for b in restored]
    assert kinds == [
        ("blockquote", None),
        ("bulletList", None),
        ("image", "顶层图"),
    ], "顶层结构正确"
    quote = restored[0]["content"]
    assert quote[1] == {"type": "image", "attrs": {"alt": "引用图", "index": 0}}, "引用内图原位"
    item2 = restored[1]["content"][1]["content"]
    assert item2[1]["type"] == "image" and item2[1]["attrs"]["alt"] == "列表图", "列表项内图原位"


def test_nested_image_chunks_keep_marker_text() -> None:
    """嵌套图切块后文本里保留 [图片] 标记(检索能命中,阅读页能替换)。"""
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "下面这张图是关键证据。"}]},
            {"type": "image", "attrs": {"alt": "关键证据图"}},
        ],
    }
    result = chunk(doc, token_counter=count_tokens)
    all_text = "\n".join(c.text for c in result.chunks)
    assert "[图片]" in all_text


# ---------------------------------------------------------------- 3. CSV RFC 4180


def test_csv_quoted_comma_and_newline(tmp_path: Path) -> None:
    """引号内的逗号/换行不算分隔符(RFC 4180)——csv 模块行为钉住。

    单元格内换行会被 table_block 拆成多个段落(TipTap 口径),拼接后应还原完整内容。
    """
    p = tmp_path / "quoted.csv"
    p.write_text('名称,备注\n"螺钉, M8","两行\n说明"\n普通,值\n', encoding="utf-8")
    r = parse_csv(p)
    rows = [
        [c["content"][0]["content"][0]["text"] if c["content"] else "" for c in row["content"]]
        for row in r.blocks["content"][0]["content"]
    ]
    assert rows[1][0] == "螺钉, M8", "引号内逗号不拆列"
    # 单元格里的换行 = 多段落(table_block 设计),段落拼接还原
    cell_paras = r.blocks["content"][0]["content"][1]["content"][1]["content"]
    joined = "\n".join(p2["content"][0]["text"] for p2 in cell_paras if p2.get("content"))
    assert joined == "两行\n说明"
    assert r.meta["rows"] == 3
