"""douyin.py 测试：URL 检测 + ID 提取。"""

from __future__ import annotations

from app.service.douyin import detect_douyin, extract_douyin_id


# ---------- detect_douyin ----------


def test_detect_douyin_short_link() -> None:
    assert detect_douyin("https://v.douyin.com/uqCjtoCtXh4/") is True


def test_detect_douyin_full_url() -> None:
    assert detect_douyin("https://www.douyin.com/video/7467293802585378048") is True


def test_detect_douyin_iesdouyin() -> None:
    assert detect_douyin("https://www.iesdouyin.com/share/video/123/") is True


def test_detect_douyin_share_text() -> None:
    """抖音分享文本里包含链接，也应该能识别。"""
    text = "6.94 复制打开抖音，看看【洪涛同学LovTech的作品】 https://v.douyin.com/uqCjtoCtXh4/"
    assert detect_douyin(text) is True


def test_detect_not_douyin_bilibili() -> None:
    assert detect_douyin("https://www.bilibili.com/video/BV1GJ411x7h7/") is False


def test_detect_not_douyin_b23() -> None:
    assert detect_douyin("https://b23.tv/xv1aGHB") is False


def test_detect_not_douyin_youtube() -> None:
    assert detect_douyin("https://www.youtube.com/watch?v=abc") is False


# ---------- extract_douyin_id ----------


def test_extract_douyin_id_from_full_url() -> None:
    url = "https://www.douyin.com/video/7467293802585378048"
    assert extract_douyin_id(url) == "7467293802585378048"


def test_extract_douyin_id_from_full_url_with_query() -> None:
    url = "https://www.douyin.com/video/7467293802585378048?previous_page=app_code_link"
    assert extract_douyin_id(url) == "7467293802585378048"


def test_extract_douyin_id_from_short_link() -> None:
    """短链无法直接提取 ID，用 URL hash 作占位（固定输入→固定输出）。"""
    url = "https://v.douyin.com/uqCjtoCtXh4/"
    id1 = extract_douyin_id(url)
    id2 = extract_douyin_id(url)
    assert id1 is not None
    assert id1 == id2  # 幂等
    assert len(id1) == 16  # sha256 前 16 位


def test_extract_douyin_id_different_short_links_different_ids() -> None:
    id1 = extract_douyin_id("https://v.douyin.com/abc123/")
    id2 = extract_douyin_id("https://v.douyin.com/def456/")
    assert id1 != id2
