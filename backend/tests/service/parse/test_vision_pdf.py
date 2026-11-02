"""扫描件 PDF 视觉识别测试(ADR 0006 扩展):真造无文本层 PDF + 假视觉 client,不联网。

fixture 用 Pillow 把图片存成 PDF —— 这种 PDF 只有图像、没有文字层,
正好落在 pdf_parser 的扫描件判定分支上。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.service.llm.client import ChatResult, LLMError
from app.service.parse import ParseStatus
from app.service.parse.router import parse_file
from app.service.parse.vision import parse_scanned_pdf


class FakePageClient:
    """按调用次序返回不同转写(便于验证「逐页」而不是「重复同一页」)。"""

    def __init__(self, contents: list[str] | None = None, *, exc_on: set[int] | None = None):
        self.model = "fake-vision-model"
        self.calls: list[dict] = []
        self._contents = contents or []
        self._exc_on = exc_on or set()

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        idx = len(self.calls)
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if idx in self._exc_on:
            raise LLMError(f"上游 500(第 {idx + 1} 次调用)")
        content = self._contents[idx] if idx < len(self._contents) else f"第 {idx + 1} 页内容"
        return ChatResult(content=content, model=self.model)


def _make_scanned_pdf(tmp_path: Path, pages: int = 2, name: str = "scan.pdf") -> Path:
    """图片存 PDF:Pillow 产出的这种 PDF 没有文字层(扫描件同构)。"""
    from PIL import Image, ImageDraw

    images = []
    for i in range(pages):
        img = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(img)
        draw.text((40, 60), f"Page {i + 1}", fill="black")
        draw.rectangle((40, 100, 560, 140), outline="black")
        images.append(img)
    path = tmp_path / name
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])
    return path


def _make_text_pdf(tmp_path: Path) -> Path:
    """有文本层的 PDF(正常 PDF 路径的对照样本)。"""
    path = tmp_path / "text.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "This PDF has a real text layer for parsing.", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_fixture_really_has_no_text_layer(tmp_path: Path) -> None:
    """先自证样本:没有文字层,否则下面所有「走了视觉」的断言都是假的。"""
    pdf = _make_scanned_pdf(tmp_path, pages=2)
    doc = pymupdf.open(pdf)
    texts = [page.get_text().strip() for page in doc]
    doc.close()
    assert texts == ["", ""], "Pillow 造的 PDF 必须无文字层,才对得上扫描件分支"


def _heading_texts(blocks: dict) -> list[str]:
    return [
        node["content"][0]["text"]
        for node in blocks["content"]
        if node.get("type") == "heading" and node.get("content")
    ]


def test_scanned_pdf_transcribes_every_page(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=2)
    client = FakePageClient(["# 第一页手写\n\n滞回比较器阈值。", "第二页:时序图说明。"])
    r = parse_scanned_pdf(pdf, client=client)
    assert r.parse_status == ParseStatus.OK
    assert len(client.calls) == 2, "两页就该调两次视觉,不多不少"
    assert "滞回比较器阈值" in str(r.blocks), "第一页内容进正文"
    assert "时序图说明" in str(r.blocks), "第二页内容进正文"
    assert r.meta["transcribed_pages"] == 2
    assert r.meta["pages"] == 2
    assert r.meta["engine"].startswith("pdf-vision(")
    # 页码标记是普通段落,不能是 heading —— 否则笔记标题会变成「第 1 页」
    assert _heading_texts(r.blocks) == ["第一页手写"]
    assert "—— 第 1 页 ——" in str(r.blocks)
    assert "—— 第 2 页 ——" in str(r.blocks)


def test_single_page_pdf_has_no_page_heading(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=1, name="one.pdf")
    client = FakePageClient(["只有一页的转写。"])
    r = parse_scanned_pdf(pdf, client=client)
    assert r.parse_status == ParseStatus.OK
    assert not [n for n in r.blocks["content"] if n.get("type") == "heading"], (
        "单页 PDF 不该有「第 1 页」噪音标题"
    )


def test_partial_page_failure_keeps_going(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=3)
    client = FakePageClient(["第一页好。", "", "第三页好。"])  # 第 2 页空 content = 失败
    r = parse_scanned_pdf(pdf, client=client)
    assert r.parse_status == ParseStatus.OK, "部分页失败不影响整体入库"
    assert r.meta["transcribed_pages"] == 2
    assert r.meta["failed_pages"] == [2]
    assert any("第 2 页识别失败" in w for w in r.warnings), "失败页要留痕"


def test_llm_error_on_one_page_degrades_page_only(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=2)
    client = FakePageClient(exc_on={0})
    r = parse_scanned_pdf(pdf, client=client)
    assert r.parse_status == ParseStatus.OK
    assert r.meta["failed_pages"] == [1]
    assert r.meta["transcribed_pages"] == 1


def test_all_pages_fail_degrades_to_no_text_expected(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=2)
    client = FakePageClient(exc_on={0, 1})
    r = parse_scanned_pdf(pdf, client=client)
    assert r.parse_status == ParseStatus.NO_TEXT_EXPECTED
    assert "都没能识别" in (r.message or "")
    assert r.meta["failed_pages"] == [1, 2]


def test_page_cap_warns_instead_of_silent_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.service.parse.vision.PDF_VISION_MAX_PAGES", 1)
    pdf = _make_scanned_pdf(tmp_path, pages=3)
    client = FakePageClient()
    r = parse_scanned_pdf(pdf, client=client)
    assert len(client.calls) == 1, "上限 1 页就只烧 1 次视觉调用"
    assert r.parse_status == ParseStatus.OK
    assert any("只识别了前 1 页" in w for w in r.warnings), "截断必须明说,不许静默"


def test_oversize_rendered_page_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """渲染后超过上限且降 DPI 也塞不下 → 跳过该页(不烧视觉调用)。"""
    monkeypatch.setattr("app.service.parse.vision.VISION_MAX_BYTES", 16)
    pdf = _make_scanned_pdf(tmp_path, pages=1)
    client = FakePageClient()
    r = parse_scanned_pdf(pdf, client=client)
    assert client.calls == [], "超限页不该发视觉请求"
    assert r.parse_status == ParseStatus.NO_TEXT_EXPECTED
    assert r.meta["overlimit_pages"] == [1]


def test_router_scanned_pdf_with_client_goes_vision(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=1)
    client = FakePageClient(["扫描件转写正文。够长一点,好过价值闸。"])
    r = parse_file(pdf, client=client)
    assert r.parse_status == ParseStatus.OK
    assert r.meta["engine"].startswith("pdf-vision(")
    assert len(client.calls) == 1


def test_router_scanned_pdf_without_client_keeps_old_semantics(tmp_path: Path) -> None:
    pdf = _make_scanned_pdf(tmp_path, pages=1)
    r = parse_file(pdf)
    assert r.parse_status == ParseStatus.NO_TEXT_EXPECTED
    assert "未检测到文本层" in (r.message or "")
    assert r.meta["per_page_chars"] == [0]


def test_router_text_pdf_never_calls_vision(tmp_path: Path) -> None:
    """有文本层的 PDF 是正常解析路径:client 在场也不该被调用(不白花钱)。"""
    pdf = _make_text_pdf(tmp_path)
    client = FakePageClient(["不该出现的内容"])
    r = parse_file(pdf, client=client)
    assert r.parse_status == ParseStatus.OK
    assert client.calls == [], "有文字层就别烧视觉调用"
    assert r.meta["engine"].startswith("pymupdf")
