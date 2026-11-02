"""md 图片导入 + media 存储测试(v16)。

1. 解析层 ``parse_md(media_sink=)``:本地图存字节、相对路径解析、缺文件退 alt、外链不抓；
2. 存储层 ``store_media``/``media_path``:内容寻址、非法 key(含路径穿越)拒绝。

API 端点(``GET /api/media/{key}``)在 ``tests/api/test_media.py``——那边才有 env 夹具。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.service.media import is_media_key, media_path, store_media
from app.service.parse import ParseStatus
from app.service.parse.md_parser import parse_md, parse_md_text

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


# ---------------------------------------------------------------- 解析层


def _sink_into(tmp_path: Path) -> tuple[list[tuple[bytes, str]], object]:
    """假 sink:记下存入内容,返回可预测的 key。"""
    stored: list[tuple[bytes, str]] = []

    def sink(data: bytes, alt: str) -> str:
        stored.append((data, alt))
        return f"{len(stored):064x}"

    return stored, sink


def test_md_local_image_stored_and_linked(tmp_path: Path) -> None:
    (tmp_path / "pic.png").write_bytes(PNG_BYTES)
    md = tmp_path / "note.md"
    md.write_text("正文。\n\n![架构图](pic.png)\n\n后续。\n", encoding="utf-8")

    stored, sink = _sink_into(tmp_path)
    r = parse_md(md, media_sink=sink)  # type: ignore[arg-type]

    assert r.parse_status == ParseStatus.OK
    assert len(stored) == 1 and stored[0][0] == PNG_BYTES, "图片字节被存下"
    images = [b for b in r.blocks["content"] if b["type"] == "image"]
    assert len(images) == 1
    attrs = images[0]["attrs"]
    assert attrs["alt"] == "架构图"
    assert attrs["media"].startswith("0" * 63), "doc 里存的是 sink 给的 key"
    assert attrs["filename"] == "pic.png"
    assert r.meta["images"] == 1
    assert any("图片" in w for w in r.warnings)
    # 图片不在段落里,而是独立的块级节点(前后段落被拆开)
    types = [b["type"] for b in r.blocks["content"]]
    assert types == ["paragraph", "image", "paragraph"]


def test_md_image_subdirectory_path(tmp_path: Path) -> None:
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "a.png").write_bytes(PNG_BYTES)
    md = tmp_path / "note.md"
    md.write_text("![子目录图](img/a.png)\n", encoding="utf-8")
    stored, sink = _sink_into(tmp_path)
    r = parse_md(md, media_sink=sink)  # type: ignore[arg-type]
    assert len(stored) == 1
    assert [b for b in r.blocks["content"] if b["type"] == "image"]


def test_md_missing_image_falls_back_to_alt(tmp_path: Path) -> None:
    md = tmp_path / "note.md"
    md.write_text("看图 ![缺的图](nope.png) 结束。\n", encoding="utf-8")
    stored, sink = _sink_into(tmp_path)
    r = parse_md(md, media_sink=sink)  # type: ignore[arg-type]
    assert stored == [], "文件不存在就不该存"
    assert not [b for b in r.blocks["content"] if b["type"] == "image"]
    assert "缺的图" in str(r.blocks), "替代文字保留,正文不丢"
    assert any("未能导入" in w for w in r.warnings), "必须明确告知"


def test_md_external_image_not_fetched(tmp_path: Path) -> None:
    md = tmp_path / "note.md"
    md.write_text("![外链](https://example.com/x.png)\n", encoding="utf-8")
    stored, sink = _sink_into(tmp_path)
    r = parse_md(md, media_sink=sink)  # type: ignore[arg-type]
    assert stored == [], "G0 不做网络 IO,外链不下载"
    images = [b for b in r.blocks["content"] if b["type"] == "image"]
    assert images and images[0]["attrs"]["src"] == "https://example.com/x.png", (
        "外链保留引用而不是丢掉"
    )


def test_md_without_sink_keeps_src(tmp_path: Path) -> None:
    (tmp_path / "pic.png").write_bytes(PNG_BYTES)
    md = tmp_path / "note.md"
    md.write_text("![图](pic.png)\n", encoding="utf-8")
    r = parse_md(md)
    images = [b for b in r.blocks["content"] if b["type"] == "image"]
    assert images and "media" not in images[0]["attrs"]


def test_parse_md_text_default_resolver_keeps_image() -> None:
    """不走文件系统时,图片节点仍成立(不再展平成 alt 文本)。"""
    content, _chars = parse_md_text("前 ![alt文本](x.png) 后\n")
    assert [b["type"] for b in content] == ["paragraph", "image", "paragraph"]


# ---------------------------------------------------------------- media 存储


def test_store_media_content_addressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.media.MEDIA_ROOT", tmp_path)
    key1 = store_media(PNG_BYTES)
    key2 = store_media(PNG_BYTES)
    assert key1 == key2, "同内容同 key"
    assert is_media_key(key1)
    path = media_path(key1)
    assert path is not None and path.read_bytes() == PNG_BYTES


@pytest.mark.parametrize(
    "bad",
    ["", "../etc/passwd", "a" * 63, "A" * 64, "z" * 64, "0" * 64],
)
def test_media_path_rejects_bad_key(bad: str) -> None:
    assert media_path(bad) is None, "非法 key(含路径穿越)必须拒绝"
