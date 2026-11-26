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


# ── 封面提取（2026-11-26，抖音海报卡用）──


def test_pick_cover_url_prefers_origin_cover() -> None:
    """origin_cover 最清晰，优先；其次 cover，最后 dynamic_cover。"""
    from app.service.douyin_extractor import _pick_cover_url

    aweme = {
        "video": {
            "origin_cover": {"url_list": ["https://origin.jpg"]},
            "cover": {"url_list": ["https://cover.jpg"]},
        }
    }
    assert _pick_cover_url(aweme) == "https://origin.jpg"

    aweme2 = {"video": {"cover": {"url_list": ["https://cover.jpg"]}}}
    assert _pick_cover_url(aweme2) == "https://cover.jpg"

    aweme3 = {"video": {"dynamic_cover": {"url_list": ["https://dyn.jpg"]}}}
    assert _pick_cover_url(aweme3) == "https://dyn.jpg"


def test_pick_cover_url_skips_non_http_and_empty() -> None:
    """候选数组里非 http 地址跳过；全没有 / 字段缺失 → None（不抛错）。"""
    from app.service.douyin_extractor import _pick_cover_url

    assert _pick_cover_url({"video": {"cover": {"url_list": ["ftp://x", "https://ok.jpg"]}}}) == (
        "https://ok.jpg"
    )
    assert _pick_cover_url({"video": {}}) is None
    assert _pick_cover_url({}) is None
    # 字段存在但不是 dict（平台改结构）→ 跳过，不崩
    assert _pick_cover_url({"video": {"cover": "https://bare-string.jpg"}}) is None


def test_download_cover_returns_media_key(monkeypatch, tmp_path) -> None:
    """封面下载后存进 media，返回内容寻址 key（不是抖音原始 URL）。

    关键断言：抖音封面是**两周过期的签名链**，所以绝不能把原 URL 存进 DB/parse_meta
    ——那会变成埋两周后爆的裂图。这里确认返回值是 sha256 hex（内容寻址 key）。
    """
    import app.service.douyin_extractor as de
    import app.service.media as media_mod

    payload = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    monkeypatch.setattr(media_mod, "MEDIA_ROOT", tmp_path)

    class FakeResp:
        def __init__(self) -> None:
            self.data = payload

        def read(self) -> bytes:
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    monkeypatch.setattr(de.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    key = de._download_cover("https://p3.douyinpic.com/xxx.jpg")

    assert key is not None
    assert len(key) == 64  # sha256 hex
    assert all(c in "0123456789abcdef" for c in key)
    # 真的落盘了
    assert (tmp_path / key[:2] / key).exists()


def test_download_cover_failure_returns_none(monkeypatch) -> None:
    """下载失败只返回 None —— 封面是锦上添花，不能拖垮整个导入。"""
    import app.service.douyin_extractor as de

    def boom(*a, **k):
        raise OSError("网络挂了")

    monkeypatch.setattr(de.urllib.request, "urlopen", boom)
    assert de._download_cover("https://p3.douyinpic.com/xxx.jpg") is None


def test_cover_cb_fires_before_video_download() -> None:
    """封面回调必须在**下载视频之前**触发（2026-11-26 实测坑）。

    现场：40 分钟视频从粘链接到导入完成要十几分钟，而封面在下载前就拿到了。
    最初把封面当返回值交给 worker，worker 要等整个 extract_video_content
    （含 whisper 转写 + 抽帧）结束才写库 → 转写阶段前端 video_cover_url 仍是 None，
    海报卡迟迟不出现。

    用**源码顺序断言**而不是跑真网络：fetch_douyin_video 要开浏览器，
    单测里跑它既慢又脆。要守的契约就是「先回调、后下载」这个顺序，
    直接断言实现位置最直接。
    """
    from pathlib import Path

    from app.service import douyin_extractor as de

    src = Path(de.__file__).read_text(encoding="utf-8")
    # 只取 fetch_douyin_video 的函数体（到下一个顶层 def 之前）
    fetch_body = src.split("def fetch_douyin_video", 1)[1]
    fetch_body = fetch_body.split(chr(10) + "def ", 1)[0]
    cb_pos = fetch_body.find("cover_cb(cover_key)")
    dl_pos = fetch_body.find("_download(play_url, video_path)")
    assert cb_pos != -1, "fetch_douyin_video 里必须调用 cover_cb"
    assert dl_pos != -1, "fetch_douyin_video 里必须调用 _download"
    assert cb_pos < dl_pos, (
        f"cover_cb(位置 {cb_pos}) 必须在 _download(位置 {dl_pos}) 之前调用 —— "
        "封面几 KB 且先拿到，晚调用会让海报卡白等十几分钟"
    )
