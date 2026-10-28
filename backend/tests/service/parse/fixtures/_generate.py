"""生成 tests/service/parse/fixtures/ 下的二进制测试件(pdf/docx/pptx/png/txt)。

随仓库入库的 fixture 全部由此脚本可复现:
    cd backend && uv run python tests/service/parse/fixtures/_generate.py
不依赖 eval/samples(backend 不得耦合 eval 目录)。
"""

import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent

HEADER = "智能笔记库 · 内部评审资料"
FOOTER = "第 {page} 页 共 2 页"
WATERMARK = "内部资料"

PARA_1 = "中文文档解析是知识库系统的第一道闸门:丢一个字,检索就少一条召回。"
PARA_2 = "表格之后的正文要能正确续接,不能与表头粘连。"

# 表格第 4 行故意复刻真实样本的"顿号被挤成独立行"形态:
# 正文以空格占位,标点单独画在下方 → find_tables 抽出 '明确目标 范围 规模 系统类型\n、、、'
TABLE = [
    ["对比维度", "一次性集成", "增量式集成"],
    ["优点", "效率高,简单易行", "尽早验证主要控制逻辑"],
    ["所需模块", "大量桩模块和驱动模块", "只需桩模块,无需驱动模块"],
    ["核心内容", "明确目标 范围 规模 系统类型", "确定功能 非功能需求 用例"],
]
TABLE_PUNCT = ["、、、", "、、"]  # 第 4 行两格的下方标点,数量与正文空格数一致


def gen_zh_pdf() -> None:
    doc = pymupdf.open()
    col_x = [72, 202, 372, 540]  # 列宽要装下第 4 行的长单元格,防溢出被 bbox 裁字

    for page_no in (1, 2):
        page = doc.new_page()
        page.insert_text((72, 40), HEADER, fontsize=10, fontname="china-s", color=(0.4, 0.4, 0.4))
        page.insert_text(
            (72, page.rect.height - 36),
            FOOTER.format(page=page_no),
            fontsize=10,
            fontname="china-s",
            color=(0.4, 0.4, 0.4),
        )
        for point in ((150, 300), (150, 600)):
            page.insert_text(
                point,
                WATERMARK,
                fontsize=48,
                fontname="china-s",
                color=(0.85, 0.85, 0.85),
                morph=(pymupdf.Point(*point), pymupdf.Matrix(45)),
            )

    page1 = doc[0]
    page1.insert_text((72, 90), PARA_1, fontsize=12, fontname="china-s")

    y0 = 120
    row_h = [28, 28, 28, 44]  # 第 4 行加高,容纳正文 + 独立标点行
    row_y = [y0]
    for h in row_h:
        row_y.append(row_y[-1] + h)
    for x in col_x:
        page1.draw_line((x, row_y[0]), (x, row_y[-1]), color=(0, 0, 0), width=0.7)
    for yy in row_y:
        page1.draw_line((col_x[0], yy), (col_x[-1], yy), color=(0, 0, 0), width=0.7)
    for r, row in enumerate(TABLE):
        for c, cell in enumerate(row):
            page1.insert_text(
                (col_x[c] + 6, row_y[r] + 19),
                cell,
                fontsize=11,
                fontname="china-s",
            )
        if r == 3:  # 标点行:更小字号、更低位置,复刻真实样本的错位形态
            for c, puncts in enumerate(TABLE_PUNCT, start=1):
                page1.insert_text(
                    (col_x[c] + 6, row_y[r] + 36),
                    puncts,
                    fontsize=9,
                    fontname="china-s",
                )

    doc[1].insert_text((72, 90), PARA_2, fontsize=12, fontname="china-s")
    doc.save(HERE / "zh.pdf")
    doc.close()


def gen_scan_pdf() -> None:
    src = pymupdf.open(HERE / "zh.pdf")
    scan = pymupdf.open()
    page = scan.new_page(width=src[0].rect.width, height=src[0].rect.height)
    pix = src[0].get_pixmap(matrix=pymupdf.Matrix(120 / 72, 120 / 72))
    pix.save(HERE / "photo.png")  # 顺手产出路由图片测试用的 photo.png
    page.insert_image(page.rect, filename=str(HERE / "photo.png"))
    scan.save(HERE / "scan.pdf", garbage=4, deflate=True)
    scan.close()
    src.close()


def gen_docx() -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("一级标题:解析测试", level=1)
    p = doc.add_paragraph()
    p.add_run("普通文本,")
    bold = p.add_run("加粗文本,")
    bold.bold = True
    italic = p.add_run("斜体文本。")
    italic.italic = True

    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "姓名"
    table.cell(0, 1).text = "备注"
    table.cell(1, 0).text = "张伟"
    table.cell(1, 1).text = "期中满分"

    doc.add_picture(str(HERE / "photo.png"), width=None, height=None)
    doc.add_paragraph("图片之后的段落")
    doc.save(HERE / "doc.docx")


def gen_pptx() -> None:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])  # 标题和内容
    s1.shapes.title.text = "解析选型要点"
    body = s1.placeholders[1].text_frame
    body.text = "第一页要点"
    body.add_paragraph().text = "第二个要点"
    s1.shapes.add_picture(str(HERE / "photo.png"), Inches(7), Inches(5), height=Inches(1.2))

    s2 = prs.slides.add_slide(prs.slide_layouts[5])  # 仅标题
    s2.shapes.title.text = "第二页:表格"
    gf = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(2))
    gf.table.cell(0, 0).text = "维度"
    gf.table.cell(0, 1).text = "结论"
    gf.table.cell(1, 0).text = "PDF 文本流"
    gf.table.cell(1, 1).text = "pymupdf 更干净"
    prs.save(HERE / "doc.pptx")


MD_CONTENT = """# 解析选型测试(Markdown)

段落含 **加粗词**、*斜体词*、行内代码 `def parse()` 与[链接](https://example.com/docs)。

| 框架 | 语言 |
|---|---|
| Vue | TypeScript |
| FastAPI | Python |

- 项目甲
- 项目乙

```python
print("hello baleen")
```

> 引用的一行结论。
"""


def gen_md() -> None:
    (HERE / "doc.md").write_text(MD_CONTENT, encoding="utf-8")


def gen_txt_and_broken() -> None:
    (HERE / "utf8.txt").write_bytes("第一行:UTF-8 无 BOM。\n第二行:中文优先。\n".encode())
    (HERE / "gb18030.txt").write_bytes("旧系统导出的 GBK 文本,数字 12345。\n".encode("gb18030"))
    (HERE / "empty.txt").write_bytes(b"")
    (HERE / "broken.pdf").write_bytes(b"%PDF-1.4\n%this file is intentionally broken\n")


if __name__ == "__main__":
    gen_zh_pdf()
    gen_scan_pdf()
    gen_docx()
    gen_pptx()
    gen_md()
    gen_txt_and_broken()
    print("fixtures 生成完毕:", ", ".join(sorted(p.name for p in HERE.iterdir() if p.is_file())))
    sys.exit(0)
