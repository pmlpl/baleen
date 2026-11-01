"""视觉解析测试(G0 装眼睛,ADR 0006):假 client,不联网。"""

from __future__ import annotations

import pytest

from app.service.llm.client import ChatResult, LLMError
from app.service.parse.router import parse_file
from app.service.parse.vision import parse_image_vision

# 1x1 PNG(合法字节,够 base64 走通,不需要真图)
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


class FakeVisionClient:
    """记录调用;可注入返回内容或异常。"""

    def __init__(self, content: str = "", exc: Exception | None = None) -> None:
        self.model = "fake-vision-model"
        self.calls: list[dict] = []
        self._content = content
        self._exc = exc

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._exc is not None:
            raise self._exc
        return ChatResult(content=self._content, model=self.model)


def _png_file(tmp_path, name="shot.png", data=PNG_1X1):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_vision_ok_transcribes_to_blocks(tmp_path) -> None:
    client = FakeVisionClient(content="# 手写笔记\n\n滞回比较器,**阈值**有两个。")
    r = parse_image_vision(_png_file(tmp_path), client=client)
    assert r.parse_status.value == "ok"
    assert r.blocks["content"][0]["type"] == "heading"
    assert any("识别" in w for w in r.warnings), "必须提示内容来自视觉识别"
    assert r.meta["engine"] == "vision(fake-vision-model)"
    # 请求形状:system + user(文字 + image_url data URL)
    msgs = client.calls[0]["messages"]
    assert msgs[0]["role"] == "system"
    img_part = msgs[1]["content"][1]
    assert img_part["type"] == "image_url"
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_llm_error_degrades_to_no_text_expected(tmp_path) -> None:
    client = FakeVisionClient(exc=LLMError("上游 500"))
    r = parse_image_vision(_png_file(tmp_path), client=client)
    assert r.parse_status.value == "no_text_expected"
    assert r.meta.get("vision_error")


def test_vision_empty_content_degrades(tmp_path) -> None:
    client = FakeVisionClient(content="")
    r = parse_image_vision(_png_file(tmp_path), client=client)
    assert r.parse_status.value == "no_text_expected"


def test_vision_oversize_skips_llm(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.parse.vision.VISION_MAX_BYTES", 8)
    client = FakeVisionClient(content="# 不该被调用")
    r = parse_image_vision(_png_file(tmp_path, data=b"1234567890"), client=client)
    assert r.parse_status.value == "no_text_expected"
    assert client.calls == [], "超限图不该烧视觉调用"


def test_router_image_with_client_goes_vision(tmp_path) -> None:
    client = FakeVisionClient(content="转写正文。")
    r = parse_file(_png_file(tmp_path), client=client)
    assert r.parse_status.value == "ok"


def test_router_image_without_client_keeps_old_semantics(tmp_path) -> None:
    r = parse_file(_png_file(tmp_path))
    assert r.parse_status.value == "no_text_expected"
    assert r.meta["engine"] == "none"


def test_router_txt_unaffected(tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("普通文本。", encoding="utf-8")
    r = parse_file(p, client=FakeVisionClient(content="# 不该被调用"))
    assert r.parse_status.value == "ok"
    assert r.meta["engine"] == "txt-direct"
