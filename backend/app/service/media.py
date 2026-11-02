"""媒体(图片)存储:内容寻址落盘 + 只读访问。

md 里的本地图片在导入时经此存字节——doc 里只留 ``media`` key(sha256),
笔记详情页按 key 取原图。与源文件 blob 用同一套内容寻址思路(AGENTS §4),
但独立目录 ``storage/media/``:源文件的 blob 是「用户上传的原始文档」,
媒体是「文档里引用的图片」,生命周期与去重范围都不同。

安全:key 必须是纯 hex(64 位),路径由 key 拼出——**不接受任何用户给的路径**,
杜绝路径穿越;读不到返回 None 由 API 层转 404。
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from app.core.config import ROOT_DIR

MEDIA_ROOT = ROOT_DIR / "storage" / "media"

# 单个媒体文件上限:md 里引用的插图不该超过这个(超过多半是误引用大文件)
MAX_MEDIA_BYTES = 20 * 1024 * 1024

_HEX = set("0123456789abcdef")


def is_media_key(key: str) -> bool:
    """合法 key = 64 位小写 hex(sha256 的 hexdigest)。"""
    return len(key) == 64 and all(c in _HEX for c in key)


def store_media(data: bytes, _alt: str = "") -> str:
    """存字节,返回内容寻址 key(sha256 hex);同内容复用同一文件。

    第二个参数是 ``ImageResolver`` 的 alt(md 解析器回调约定的一部分),这里不用。
    """
    key = hashlib.sha256(data).hexdigest()
    target = MEDIA_ROOT / key[:2] / key
    if target.exists():
        return key
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return key


def media_path(key: str) -> Path | None:
    """key → 磁盘路径;key 非法或文件不存在返回 None(调用方转 404)。"""
    if not is_media_key(key):
        return None
    path = MEDIA_ROOT / key[:2] / key
    return path if path.is_file() else None


def media_mime(key: str) -> str:
    """按文件头嗅探常见图片类型;认不出按 octet-stream。"""
    path = media_path(key)
    if path is None:
        return "application/octet-stream"
    head = path.read_bytes()[:12]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    # SVG 是文本:按扩展名兜底(存储时没留扩展名,这里按内容前缀判断)
    text_head = path.read_bytes()[:200].lstrip()
    if text_head.startswith(b"<svg") or text_head.startswith(b"<?xml"):
        return "image/svg+xml"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


__all__ = [
    "MAX_MEDIA_BYTES",
    "MEDIA_ROOT",
    "is_media_key",
    "media_mime",
    "media_path",
    "store_media",
]
