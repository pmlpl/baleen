"""HTML 解析测试:正文提取/data URI 图片/外链降级/编码容错,零联网。"""

from __future__ import annotations

import base64

from app.service.parse import blocks
from app.service.parse.router import parse_file

# 1x1 PNG(与 test_vision.py 同款,合法字节够 base64 走通)
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


def _html_file(tmp_path, body: str, name: str = "resume.html", encoding: str = "utf-8"):
    p = tmp_path / name
    p.write_bytes(body.encode(encoding))
    return p


def test_html_text_and_headings(tmp_path) -> None:
    p = _html_file(
        tmp_path,
        "<html><head><style>.a{color:red}</style><title>页标题</title></head>"
        "<body><h1>张三</h1><p>前端工程师</p><div>邮箱:z@x.com</div></body></html>",
    )
    r = parse_file(p)
    assert r.parse_status.value == "ok"
    text = blocks.flatten_text(r.blocks)
    assert "张三" in text and "前端工程师" in text and "z@x.com" in text
    assert "color:red" not in text, "style 不许进正文"
    assert "页标题" not in text, "head 内 title 不许进正文"
    # h1 → heading 块
    assert r.blocks["content"][0]["type"] == "heading"
    assert r.blocks["content"][0]["attrs"]["level"] == 1


def test_html_data_uri_image_collected_via_sink(tmp_path) -> None:
    b64 = base64.b64encode(PNG_1X1).decode()
    html = (
        f'<html><body><p>你好</p><img src="data:image/png;base64,{b64}" alt="照片"></body></html>'
    )
    collected: list[tuple[bytes, str]] = []

    def sink(data: bytes, alt: str) -> str:
        collected.append((data, alt))
        return f"k{len(collected)}"

    r = parse_file(_html_file(tmp_path, html), media_sink=sink)
    text = blocks.flatten_text(r.blocks)
    assert "你好" in text
    assert "base64" not in text, "图片字节不许进正文"
    assert len(collected) == 1
    assert collected[0][0] == PNG_1X1
    assert collected[0][1] == "照片"
    assert r.meta["images"] == 1


def test_html_data_uri_without_sink_degrades_to_alt(tmp_path) -> None:
    b64 = base64.b64encode(PNG_1X1).decode()
    html = f'<p>前</p><img src="data:image/png;base64,{b64}" alt="头像"><p>后</p>'
    r = parse_file(_html_file(tmp_path, html))
    text = blocks.flatten_text(r.blocks)
    assert "头像" in text
    assert "base64" not in text
    assert any("未导入" in w for w in r.warnings)


def test_html_external_image_ignored_with_warning(tmp_path) -> None:
    html = '<p>正文</p><img src="https://cdn.example.com/a.png" alt="外链图">'
    r = parse_file(_html_file(tmp_path, html))
    text = blocks.flatten_text(r.blocks)
    assert "外链图" in text
    assert any("未导入" in w for w in r.warnings)


def test_html_oversized_image_degrades_to_alt(tmp_path) -> None:
    """sink 因单张超限拒收(抛 ValueError):同「无 sink」口径退 alt,不炸 G0(A1)。"""
    b64 = base64.b64encode(PNG_1X1).decode()
    html = f'<p>前</p><img src="data:image/png;base64,{b64}" alt="大图"><p>后</p>'

    def sink(data: bytes, alt: str) -> str:
        raise ValueError("超过单张上限")

    r = parse_file(_html_file(tmp_path, html), media_sink=sink)
    text = blocks.flatten_text(r.blocks)
    assert r.parse_status.value == "ok"
    assert "大图" in text, "替代文字保留"
    assert r.meta["images"] == 0
    assert any("单张大小上限" in w for w in r.warnings), "必须明确告知拒收原因"


def test_html_gb18030_decoded(tmp_path) -> None:
    p = tmp_path / "legacy.html"
    p.write_bytes("<p>中文简历内容</p>".encode("gb18030"))
    r = parse_file(p)
    assert "中文简历内容" in blocks.flatten_text(r.blocks)
    assert any("GB18030" in w for w in r.warnings)


def test_html_empty_body_ok(tmp_path) -> None:
    r = parse_file(_html_file(tmp_path, "<html><head><style>x</style></head><body></body></html>"))
    assert r.parse_status.value == "ok"
    assert blocks.flatten_text(r.blocks) == ""


def test_html_meta_link_in_head_do_not_swallow_body(tmp_path) -> None:
    # head 里的 meta/link 是 void 标签(无 endtag),不许让 skip_depth 卡住吞掉整个 body
    # (实测一份带 2 个 meta 的简历导致正文全空)
    html = (
        '<html><head><meta charset="UTF-8"><meta name="x" content="y">'
        '<link rel="stylesheet" href="a.css"><title>t</title></head>'
        "<body><h1>正文标题</h1><p>正文内容</p></body></html>"
    )
    r = parse_file(_html_file(tmp_path, html))
    text = blocks.flatten_text(r.blocks)
    assert "正文标题" in text and "正文内容" in text
    assert "t" not in text, "head 内 title 不许进正文"


def test_html_unclosed_head_falls_back_coarse(tmp_path) -> None:
    """挂账①:未闭合的 <head> 会让 skip_depth 永不归零,首轮整篇被吞 → text=""。

    Word「另存为 HTML」的真实形态(head 无闭合标签)。首轮空 → 粗提取重喂
    (skip 集去掉 head/template),正文必须回来,且警告留痕。
    """
    html = (
        "<html><head><meta charset='UTF-8'><title>张三的简历\n"  # head 从未闭合
        "<body><h1>张三</h1><p>前端工程师,邮箱 z@x.com</p></body></html>"
    )
    r = parse_file(_html_file(tmp_path, html))
    text = blocks.flatten_text(r.blocks)
    assert "前端工程师" in text and "z@x.com" in text, "粗提取必须救回正文"
    assert any("回退粗提取" in w for w in r.warnings)
    # title 未闭合时无法按跳过处理——回退路径里 title 一行元数据可能混进正文
    # (一行元数据换整篇正文,可接受;见 _COARSE_SKIP_TAGS 注释)


def test_html_template_body_falls_back_coarse(tmp_path) -> None:
    """挂账①同批:导出器把正文裹进 <template>(首轮按惰性内容跳过)→ 粗提取救回。"""
    html = (
        "<html><body><template><h1>工作经历</h1>"
        "<p>2023-2026 后端工程师</p></template></body></html>"
    )
    r = parse_file(_html_file(tmp_path, html))
    assert "后端工程师" in blocks.flatten_text(r.blocks)
    assert any("回退粗提取" in w for w in r.warnings)


def test_html_utf16_bom_decoded(tmp_path) -> None:
    """挂账①同批:UTF-16LE+BOM(Word 另存常见)在 utf-8/gb18030 下不报错,
    LE 的 ASCII 解成 NUL 夹层乱码——必须先认 BOM,正文才有意义。"""
    p = tmp_path / "resume_utf16.html"
    p.write_bytes(
        "<html><head><title>r</title></head><body><p>张三 男 本科</p></body></html>".encode(
            "utf-16"
        )
    )
    r = parse_file(p)
    text = blocks.flatten_text(r.blocks)
    assert "张三 男 本科" in text
    assert "\x00" not in text, "NUL 夹层乱码不许再出现"
    assert any("UTF-16" in w for w in r.warnings)


def test_html_healthy_doc_no_coarse_fallback(tmp_path) -> None:
    """健康文档不触发粗提取路径(不产『回退粗提取』警告)。"""
    html = (
        "<html><head><title>页标题</title></head><body><h1>张三</h1><p>前端工程师</p></body></html>"
    )
    r = parse_file(_html_file(tmp_path, html))
    assert "前端工程师" in blocks.flatten_text(r.blocks)
    assert not any("回退粗提取" in w for w in r.warnings)
