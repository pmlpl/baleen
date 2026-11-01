"""G0 解析模块单测。

fixture 全部自造入库(tests/service/parse/fixtures/,可由 _generate.py 复现),
不依赖 eval/samples(backend 不得耦合 eval 目录);真实样本走 scripts/parse_smoke.py。
"""

from pathlib import Path

from app.service.parse import ParseStatus, parse_file
from app.service.parse.blocks import flatten_text

FIX = Path(__file__).parent / "fixtures"


def find_blocks(doc: dict, kind: str) -> list[dict]:
    return [b for b in doc["content"] if b["type"] == kind]


def table_cells(table_block: dict) -> list[list[str]]:
    return [
        [c["content"][0]["content"][0]["text"] if c["content"] else "" for c in row["content"]]
        for row in table_block["content"]
    ]


class TestPdf:
    def test_ok_and_noise_filtered(self):
        r = parse_file(FIX / "zh.pdf")
        assert r.parse_status == ParseStatus.OK
        text = flatten_text(r.blocks)
        assert "中文文档解析是知识库系统的第一道闸门" in text
        assert "表格之后的正文要能正确续接" in text
        assert "内部资料" not in text, "水印字符不应残留"
        assert "内部评审资料" not in text, "页眉不应残留"
        assert "共2页" not in text.replace(" ", ""), "页脚不应残留"
        assert any("水印" in w for w in r.warnings)
        assert any("页眉/页脚" in w for w in r.warnings)
        assert r.meta["tables"] == 1
        assert r.meta["pages"] == 2

    def test_table_punct_refill(self):
        """顿号归位(ADR §5):被挤成独立行的标点回填,正常标点不动。"""
        r = parse_file(FIX / "zh.pdf")
        cells = table_cells(find_blocks(r.blocks, "table")[0])
        assert cells[3][1] == "明确目标、范围、规模、系统类型"
        assert cells[3][2] == "确定功能、非功能需求、用例"
        assert cells[1][1] == "效率高,简单易行"
        assert cells[0] == ["对比维度", "一次性集成", "增量式集成"]

    def test_scan_pdf_no_text_expected(self):
        r = parse_file(FIX / "scan.pdf")
        assert r.parse_status == ParseStatus.NO_TEXT_EXPECTED
        assert r.blocks["content"] == []
        assert "OCR" in (r.message or "")

    def test_broken_pdf_error(self):
        r = parse_file(FIX / "broken.pdf")
        assert r.parse_status == ParseStatus.ERROR
        assert r.message


class TestDocx:
    def test_order_marks_table_image(self):
        r = parse_file(FIX / "doc.docx")
        assert r.parse_status == ParseStatus.OK
        assert [b["type"] for b in r.blocks["content"]] == [
            "heading",
            "paragraph",
            "table",
            "image",
            "paragraph",
        ]
        heading = r.blocks["content"][0]
        assert heading["attrs"]["level"] == 1
        assert heading["content"][0]["text"] == "一级标题:解析测试"
        inlines = r.blocks["content"][1]["content"]
        assert {"type": "bold"} in inlines[1]["marks"]
        assert {"type": "italic"} in inlines[2]["marks"]
        cells = table_cells(r.blocks["content"][2])
        assert cells[0] == ["姓名", "备注"]
        assert cells[1][0] == "张伟"
        assert r.meta["images"] == 1


class TestPptx:
    def test_slides_title_table_image(self):
        r = parse_file(FIX / "doc.pptx")
        assert r.parse_status == ParseStatus.OK
        assert r.meta["slides"] == 2
        assert r.meta["images"] == 1
        assert r.meta["tables"] == 1
        assert [b["type"] for b in r.blocks["content"]] == [
            "heading",
            "paragraph",
            "paragraph",
            "image",
            "heading",
            "table",
        ]
        assert r.blocks["content"][0]["content"][0]["text"] == "解析选型要点"
        cells = table_cells(find_blocks(r.blocks, "table")[0])
        assert cells[1] == ["PDF 文本流", "pymupdf 更干净"]


class TestMd:
    def test_blocks_and_marks(self):
        r = parse_file(FIX / "doc.md")
        assert r.parse_status == ParseStatus.OK
        assert [b["type"] for b in r.blocks["content"]] == [
            "heading",
            "paragraph",
            "table",
            "bulletList",
            "codeBlock",
            "blockquote",
        ]
        marks = [
            (n["text"], [m["type"] for m in n.get("marks", [])])
            for n in r.blocks["content"][1]["content"]
        ]
        assert ("加粗词", ["bold"]) in marks
        assert ("def parse()", ["code"]) in marks
        assert any("link" in ms for _, ms in marks)
        code = find_blocks(r.blocks, "codeBlock")[0]
        assert code["attrs"]["language"] == "python"
        assert len(find_blocks(r.blocks, "bulletList")[0]["content"]) == 2
        cells = table_cells(find_blocks(r.blocks, "table")[0])
        assert cells[1] == ["Vue", "TypeScript"]


class TestTxt:
    def test_utf8(self):
        r = parse_file(FIX / "utf8.txt")
        assert r.parse_status == ParseStatus.OK
        assert "第一行:UTF-8 无 BOM。" in flatten_text(r.blocks)
        assert r.warnings == []

    def test_gb18030_with_warning(self):
        r = parse_file(FIX / "gb18030.txt")
        assert r.parse_status == ParseStatus.OK
        assert "旧系统导出的 GBK 文本" in flatten_text(r.blocks)
        assert any("GB18030" in w for w in r.warnings)

    def test_empty_ok_not_error(self):
        """0 字节文件:ok + 空 blocks + chars=0,不许返回 error(ADR §2.1)。"""
        r = parse_file(FIX / "empty.txt")
        assert r.parse_status == ParseStatus.OK
        assert r.blocks["content"] == []
        assert r.meta["chars"] == 0
        assert r.message is None


class TestRouter:
    def test_doc_unsupported_with_hint(self, tmp_path: Path):
        f = tmp_path / "a.doc"
        f.write_bytes(b"x")
        r = parse_file(f)
        assert r.parse_status == ParseStatus.UNSUPPORTED_FORMAT
        assert "请用 Word/WPS 另存为 .docx" in r.message

    def test_xlsx_unsupported(self, tmp_path: Path):
        f = tmp_path / "a.xlsx"
        f.write_bytes(b"x")
        assert parse_file(f).parse_status == ParseStatus.UNSUPPORTED_FORMAT

    def test_image_no_text_expected(self):
        """纯图片无 client(未配 key)→ no_text_expected 而非 unsupported(ADR §2.1/0006)。"""
        r = parse_file(FIX / "photo.png")
        assert r.parse_status == ParseStatus.NO_TEXT_EXPECTED
        assert "识别" in (r.message or "")  # 提示里说明跳过了视觉识别(0006)

    def test_unknown_ext(self, tmp_path: Path):
        f = tmp_path / "a.zip"
        f.write_bytes(b"x")
        r = parse_file(f)
        assert r.parse_status == ParseStatus.UNSUPPORTED_FORMAT
        assert ".zip" in r.message

    def test_uppercase_ext(self, tmp_path: Path):
        f = tmp_path / "A.TXT"
        f.write_bytes("内容".encode())
        assert parse_file(f).parse_status == ParseStatus.OK
