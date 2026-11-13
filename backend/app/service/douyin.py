"""抖音视频 URL 检测。

元信息提取（标题、时长、作者）由 ``video_extractor.py`` 通过 yt-dlp 统一处理，
本模块只负责 URL 识别和视频 ID 提取（用于 dedupe 占位 sha）。

抖音链接格式：
- 完整 URL：``https://www.douyin.com/video/7467293802585378048``
- 分享短链：``https://v.douyin.com/uqCjtoCtXh4/``（重定向到完整 URL）
- 旧域名：``https://www.iesdouyin.com/share/video/...``
"""

from __future__ import annotations

import hashlib
import re

_DOUYIN_HOSTS = ("v.douyin.com", "www.douyin.com", "iesdouyin.com", "douyin.com")

# https://www.douyin.com/video/{数字ID}
_VIDEO_ID_RE = re.compile(r"/video/(\d+)")


def detect_douyin(url: str) -> bool:
    """检测 URL 是否为抖音链接。"""
    return any(host in url for host in _DOUYIN_HOSTS)


def extract_douyin_id(url: str) -> str | None:
    """从抖音 URL 提取视频标识（用于 dedupe 占位 sha）。

    完整 URL → 提取数字 ID；
    短链（``v.douyin.com/{code}/``）→ 无法不解开重定向就拿到 ID，用 URL hash 作占位。
    """
    m = _VIDEO_ID_RE.search(url)
    if m:
        return m.group(1)
    # 短链：用 URL 本身的 hash 作占位（展开重定向由 yt-dlp 在下载时处理）
    return hashlib.sha256(url.encode()).hexdigest()[:16]


__all__ = ["detect_douyin", "extract_douyin_id"]
