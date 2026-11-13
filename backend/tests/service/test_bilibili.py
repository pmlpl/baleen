"""bilibili.py 单测:纯函数逻辑 + monkeypatch 模拟 B 站 API,不联网。"""

from __future__ import annotations

import pytest

from app.service import bilibili
from app.service.bilibili import (
    BilibiliError,
    BilibiliResult,
    Chapter,
    extract_bvid,
    extract_page,
    fetch_bilibili,
    sanitize_filename,
    _build_markdown,
    _format_duration,
    _segments_to_paragraphs,
)


# ----------------------------------------------------------------- extract_page

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.bilibili.com/video/BV1GJ411x7h7/?p=2", 2),
        ("https://www.bilibili.com/video/BV1GJ411x7h7?p=5", 5),
        ("https://www.bilibili.com/video/BV1GJ411x7h7", None),
        ("https://www.bilibili.com/list/watchlater/?bvid=BV1GJ411x7h7", None),
    ],
)
def test_extract_page(url: str, expected: int | None) -> None:
    assert extract_page(url) == expected


# ----------------------------------------------------------------- extract_bvid

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.bilibili.com/video/BV1GJ411x7h7/", "BV1GJ411x7h7"),
        ("https://www.bilibili.com/video/BV1GJ411x7h7", "BV1GJ411x7h7"),
        ("https://www.bilibili.com/video/BV1GJ411x7h7?p=2&t=30", "BV1GJ411x7h7"),
        ("https://m.bilibili.com/video/BV1abc12345d", "BV1abc12345d"),
        ("https://b23.tv/BV1GJ411x7h7", "BV1GJ411x7h7"),
        ("随便说一段 BV1GJ411x7h7 夹在中间", "BV1GJ411x7h7"),
    ],
)
def test_extract_bvid(url: str, expected: str) -> None:
    assert extract_bvid(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://www.bilibili.com/",
        "不是链接",
        "",
    ],
)
def test_extract_bvid_none(url: str) -> None:
    assert extract_bvid(url) is None


# ----------------------------------------------------------------- _segments_to_paragraphs

def test_segments_merge_by_sentence() -> None:
    """句末标点 → 断段。"""
    segs = [
        {"from": 0.0, "to": 1.0, "content": "大家好。"},
        {"from": 1.0, "to": 2.0, "content": "今天讲闭包。"},
        {"from": 2.0, "to": 3.0, "content": "闭包是什么？"},
    ]
    result = _segments_to_paragraphs(segs)
    paragraphs = result.split("\n\n")
    assert len(paragraphs) == 3
    assert paragraphs[0] == "大家好。"
    assert paragraphs[1] == "今天讲闭包。"
    assert paragraphs[2] == "闭包是什么？"


def test_segments_merge_by_time_gap() -> None:
    """时间间隔 >3 秒 → 断段(即使没有句末标点)。"""
    segs = [
        {"from": 0.0, "to": 1.0, "content": "第一段话"},
        {"from": 5.0, "to": 6.0, "content": "第二段话"},  # 间隔 4 秒
    ]
    result = _segments_to_paragraphs(segs)
    paragraphs = result.split("\n\n")
    assert len(paragraphs) == 2
    assert paragraphs[0] == "第一段话"
    assert paragraphs[1] == "第二段话"


def test_segments_merge_consecutive() -> None:
    """无句末标点且间隔小 → 合并到同一段。"""
    segs = [
        {"from": 0.0, "to": 1.0, "content": "闭包是"},
        {"from": 1.0, "to": 2.0, "content": "函数和"},
        {"from": 2.0, "to": 3.0, "content": "其词法环境的组合"},
    ]
    result = _segments_to_paragraphs(segs)
    assert "\n\n" not in result
    assert result == "闭包是函数和其词法环境的组合"


def test_segments_skip_empty() -> None:
    """空 content 片段跳过。"""
    segs = [
        {"from": 0.0, "to": 1.0, "content": ""},
        {"from": 1.0, "to": 2.0, "content": "  "},
        {"from": 2.0, "to": 3.0, "content": "有效内容。"},
    ]
    result = _segments_to_paragraphs(segs)
    assert result == "有效内容。"


def test_segments_empty_list() -> None:
    assert _segments_to_paragraphs([]) == ""


# ----------------------------------------------------------------- _build_markdown

def _make_info(**kw) -> bilibili.BilibiliVideoInfo:
    defaults = dict(
        bvid="BV1GJ411x7h7", aid=12345, cid=67890,
        title="测试视频", description="", owner_name="UP主",
        duration=600, chapters=[],
    )
    defaults.update(kw)
    return bilibili.BilibiliVideoInfo(**defaults)


def test_build_markdown_no_chapters() -> None:
    info = _make_info(description="视频简介")
    segs = [{"from": 0.0, "to": 1.0, "content": "正文字幕。"}]
    md = _build_markdown(info, segs, "https://www.bilibili.com/video/BV1GJ411x7h7")
    assert md.startswith("# 测试视频\n")
    assert "UP主: UP主" in md
    assert "时长: 10:00" in md
    assert "视频简介" in md
    assert "正文字幕。" in md
    assert "B站原链接" in md


def test_build_markdown_with_chapters() -> None:
    info = _make_info(chapters=[
        Chapter(time=0.0, title="第一章"),
        Chapter(time=60.0, title="第二章"),
    ])
    segs = [
        {"from": 0.0, "to": 5.0, "content": "第一章内容。"},
        {"from": 60.0, "to": 65.0, "content": "第二章内容。"},
    ]
    md = _build_markdown(info, segs, "https://www.bilibili.com/video/BV1GJ411x7h7")
    assert "## 第一章" in md
    assert "## 第二章" in md
    assert "第一章内容。" in md
    assert "第二章内容。" in md


def test_build_markdown_long_description_truncated() -> None:
    info = _make_info(description="A" * 300)
    segs = [{"from": 0.0, "to": 1.0, "content": "内容。"}]
    md = _build_markdown(info, segs, "https://www.bilibili.com/video/BV1GJ411x7h7")
    # 描述截断到 200 字
    assert "A" * 201 not in md


# ----------------------------------------------------------------- _format_duration

def test_format_duration() -> None:
    assert _format_duration(0) == "0:00"
    assert _format_duration(65) == "1:05"
    assert _format_duration(3661) == "1:01:01"


# ----------------------------------------------------------------- sanitize_filename

def test_sanitize_filename() -> None:
    assert sanitize_filename("正常标题") == "正常标题"
    assert sanitize_filename("含/斜\\杠:的*标题?") == "含_斜_杠_的_标题_"
    assert sanitize_filename("") == "bilibili_video"
    assert sanitize_filename("   ") == "bilibili_video"
    assert len(sanitize_filename("A" * 200)) == 100


# ----------------------------------------------------------------- fetch_bilibili (mocked)

def _fake_view_response() -> dict:
    return {
        "code": 0,
        "data": {
            "aid": 12345,
            "cid": 67890,
            "title": "深入理解闭包",
            "desc": "JavaScript 闭包详解",
            "owner": {"name": "技术UP主"},
            "duration": 1200,
            "view_points": [
                {"from": 0, "content": "什么是闭包"},
                {"from": 600, "content": "闭包的应用"},
            ],
        },
    }


def _fake_player_response() -> dict:
    return {
        "code": 0,
        "data": {
            "subtitle": {
                "subtitles": [
                    {"lan": "zh-CN", "lan_doc": "中文(简体)", "subtitle_url": "//aisubtitle.hdslb.com/test.json"},
                    {"lan": "en", "lan_doc": "English", "subtitle_url": "//aisubtitle.hdslb.com/test_en.json"},
                ]
            }
        },
    }


def _fake_subtitle_body() -> list[dict]:
    return [
        {"from": 0.0, "to": 2.0, "content": "大家好。"},
        {"from": 2.0, "to": 5.0, "content": "今天讲闭包。"},
        {"from": 600.0, "to": 603.0, "content": "现在讲应用。"},
    ]


def test_fetch_bilibili_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 三步 API,验证返回的 markdown 结构。"""
    call_urls: list[str] = []

    def fake_api_get(url: str, *, sessdata: str = "") -> dict:
        call_urls.append(url)
        if "web-interface/view" in url:
            return _fake_view_response()
        if "player/v2" in url:
            return _fake_player_response()
        return {}

    def fake_fetch_subtitle_raw(sub_url: str) -> list[dict]:
        call_urls.append(sub_url)
        return _fake_subtitle_body()

    monkeypatch.setattr(bilibili, "_api_get", fake_api_get)
    monkeypatch.setattr(bilibili, "_fetch_subtitle_raw", fake_fetch_subtitle_raw)

    result = fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/")
    assert isinstance(result, BilibiliResult)
    assert result.info.title == "深入理解闭包"
    assert result.info.owner_name == "技术UP主"
    assert result.info.duration == 1200
    assert len(result.info.chapters) == 2

    md = result.markdown
    assert md.startswith("# 深入理解闭包")
    assert "技术UP主" in md
    assert "20:00" in md  # 1200 秒 = 20 分钟
    assert "## 什么是闭包" in md
    assert "## 闭包的应用" in md
    assert "大家好。" in md
    assert "今天讲闭包。" in md
    assert "现在讲应用。" in md

    # 优先选了中文字幕(zh-CN)
    assert any("aisubtitle" in u for u in call_urls)


def test_fetch_bilibili_multi_part(monkeypatch: pytest.MonkeyPatch) -> None:
    """多分P视频：?p=2 时用第二个分P的 cid。"""
    view_with_pages = {
        "code": 0,
        "data": {
            "aid": 12345,
            "cid": 67890,  # 第一个分P
            "title": "JavaScript课程",
            "desc": "",
            "owner": {"name": "讲师"},
            "duration": 3600,
            "pages": [
                {"cid": 67890, "page": 1, "part": "课程介绍"},
                {"cid": 67891, "page": 2, "part": "闭包详解"},
                {"cid": 67892, "page": 3, "part": "原型链"},
            ],
        },
    }

    def fake_api_get(url: str, *, sessdata: str = "") -> dict:
        if "web-interface/view" in url:
            return view_with_pages
        if "player/v2" in url:
            return {"code": 0, "data": {"subtitle": {"subtitles": [
                {"lan": "zh-CN", "subtitle_url": "//aisubtitle.hdslb.com/test.json"},
            ]}}}
        return {}

    monkeypatch.setattr(bilibili, "_api_get", fake_api_get)
    monkeypatch.setattr(bilibili, "_fetch_subtitle_raw", lambda u: [
        {"from": 0.0, "to": 2.0, "content": "闭包是函数和环境的组合。"},
    ])

    result = fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/?p=2")
    assert "闭包详解" in result.info.title  # 标题包含分P名称
    assert result.info.cid == 67891  # 用了第二个分P的 cid


def test_fetch_bilibili_multi_part_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """?p 超出分P范围 → BilibiliError。"""
    view_with_pages = {
        "code": 0,
        "data": {
            "aid": 12345, "cid": 67890, "title": "课程", "desc": "",
            "owner": {"name": "讲师"}, "duration": 3600,
            "pages": [{"cid": 67890, "page": 1, "part": "第1集"}],
        },
    }
    monkeypatch.setattr(bilibili, "_api_get", lambda url, *, sessdata="": view_with_pages)
    with pytest.raises(BilibiliError, match="超出范围"):
        fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/?p=5")


def test_fetch_bilibili_no_bvid() -> None:
    with pytest.raises(BilibiliError, match="BV 号"):
        fetch_bilibili("https://www.youtube.com/watch?v=abc")


def test_fetch_bilibili_video_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bilibili, "_api_get", lambda url, *, sessdata="": {"code": -404, "message": "视频不存在"})
    with pytest.raises(BilibiliError, match="视频信息获取失败"):
        fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/")


def test_fetch_bilibili_no_subtitle(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(url: str, *, sessdata: str = "") -> dict:
        if "web-interface/view" in url:
            return _fake_view_response()
        if "player/v2" in url:
            return {"code": 0, "data": {"subtitle": {"subtitles": []}}}
        return {}

    monkeypatch.setattr(bilibili, "_api_get", fake_api_get)
    with pytest.raises(BilibiliError, match="没有字幕"):
        fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/")


def test_fetch_bilibili_no_subtitle_with_sessdata(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(url: str, *, sessdata: str = "") -> dict:
        if "web-interface/view" in url:
            return _fake_view_response()
        if "player/v2" in url:
            return {"code": 0, "data": {"subtitle": {"subtitles": []}}}
        return {}

    monkeypatch.setattr(bilibili, "_api_get", fake_api_get)
    with pytest.raises(BilibiliError, match="SESSDATA"):
        fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/", sessdata="fake_sessdata")


def test_fetch_bilibili_player_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(url: str, *, sessdata: str = "") -> dict:
        if "web-interface/view" in url:
            return _fake_view_response()
        if "player/v2" in url:
            return {"code": -40001, "message": "参数错误"}
        return {}

    monkeypatch.setattr(bilibili, "_api_get", fake_api_get)
    with pytest.raises(BilibiliError, match="播放器信息获取失败"):
        fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/")


def test_fetch_bilibili_empty_subtitle_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(url: str, *, sessdata: str = "") -> dict:
        if "web-interface/view" in url:
            return _fake_view_response()
        if "player/v2" in url:
            return _fake_player_response()
        return {}

    monkeypatch.setattr(bilibili, "_api_get", fake_api_get)
    monkeypatch.setattr(bilibili, "_fetch_subtitle_raw", lambda sub_url: [])
    with pytest.raises(BilibiliError, match="字幕内容为空"):
        fetch_bilibili("https://www.bilibili.com/video/BV1GJ411x7h7/")
