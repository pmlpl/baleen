"""B 站视频字幕抓取(纯 HTTP,零第三方依赖)。

把 B 站视频的 CC/AI 字幕拉下来,拼成 markdown,交给现有 md 导入管线(G0→G1→G2→
切块→向量化)。不下载视频、不做语音识别——没有字幕的视频直接报错。

流程:解析 BV 号 → view 接口拿视频信息 → player/v2 拿字幕 URL → 拉字幕 JSON →
拼成 markdown(有章节按章节分 heading,字幕片段按句末标点和时间间隔合段)。

SESSDATA cookie:B 站 player/v2 接口带 SESSDATA 才能拿到大部分视频的 CC/AI 字幕。
没有 SESSDATA 时字幕列表往往为空。从 .env 的 ``BILIBILI_SESSDATA`` 读,空=不传。
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

_BVID_RE = re.compile(r"BV[a-zA-Z0-9]{10}")
_PAGE_RE = re.compile(r"[?&]p=(\d+)")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REFERER = "https://www.bilibili.com/"
_TIMEOUT = 15


class BilibiliError(Exception):
    """B 站抓取失败(无字幕 / 视频不存在 / 接口异常),由 API 层转成 400。"""


@dataclass
class Chapter:
    """视频章节(view_points):时间戳 + 标题。"""

    time: float  # 开始时间(秒)
    title: str


@dataclass
class BilibiliVideoInfo:
    bvid: str
    aid: int
    cid: int
    title: str
    description: str
    owner_name: str
    duration: int  # 秒
    chapters: list[Chapter] = field(default_factory=list)


@dataclass
class BilibiliResult:
    info: BilibiliVideoInfo
    markdown: str


def extract_bvid(url: str) -> str | None:
    """从 B 站链接里提取 BV 号。

    支持的 URL 格式:
    - https://www.bilibili.com/video/BV1GJ411x7h7/
    - https://www.bilibili.com/video/BV1GJ411x7h7?p=2
    - https://m.bilibili.com/video/BV1GJ411x7h7
    - https://b23.tv/BV1GJ411x7h7(短链已展开的情况)
    - https://www.bilibili.com/list/watchlater/?bvid=BV1GJ411x7h7
    """
    m = _BVID_RE.search(url)
    return m.group(0) if m else None


def extract_page(url: str) -> int | None:
    """从 URL 里提取分P参数 ?p=N，返回页码（从1开始）。没有则返回 None。"""
    m = _PAGE_RE.search(url)
    return int(m.group(1)) if m else None


def _resolve_short_link(url: str) -> str:
    """跟随 b23.tv 短链重定向，返回最终 URL。

    b23.tv 短链不含 BV 号，需要 HTTP 重定向到完整的 bilibili.com/video/BVxxx URL。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.url


def _api_get(url: str, *, sessdata: str = "") -> dict:
    """GET 一个 B 站 JSON 接口,返回解析后的 dict。"""
    headers: dict[str, str] = {"User-Agent": _UA, "Referer": _REFERER}
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_subtitle_raw(sub_url: str) -> list[dict]:
    """拉字幕 JSON,返回片段列表。

    B 站字幕格式有两种:
    - ``{"body": [{from, to, content}, ...]}``(ai_subtitle 常见)
    - ``[{from, to, content}, ...]``(直数组)
    """
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    req = urllib.request.Request(sub_url, headers={"User-Agent": _UA, "Referer": _REFERER})
    with urllib.request.urlopen(req, timeout=_TIMEOUT * 2) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("body", [])
    return []


def fetch_bilibili(url: str, *, sessdata: str = "") -> BilibiliResult:
    """主入口:解析 BV → 拿视频信息 → 拿字幕 → 拼 markdown。

    支持多分P视频：从 URL 解析 ?p=N，用对应分P的 cid 拉字幕。
    无字幕时抛 ``BilibiliError``;网络/接口异常直接向上抛(由 API 层兜底 502)。
    """
    bvid = extract_bvid(url)
    if not bvid:
        # 尝试解析 b23.tv 短链（重定向到完整的 bilibili.com/video/BVxxx URL）
        try:
            resolved = _resolve_short_link(url)
            bvid = extract_bvid(resolved)
            if bvid:
                url = resolved  # 用解析后的 URL（含 BV 号和 ?p= 参数）
        except Exception:
            pass
    if not bvid:
        raise BilibiliError("无法从链接中识别 BV 号,请输入完整的 B 站视频链接")

    page_num = extract_page(url)  # ?p=N，None 表示未指定

    # ① 视频信息(无需 auth)
    view = _api_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", sessdata=sessdata)
    if view.get("code") != 0:
        raise BilibiliError(f"视频信息获取失败:{view.get('message', '未知错误')}")
    d = view["data"]

    # 多分P处理：pages 数组里每个分P有自己的 cid
    pages = d.get("pages", [])
    cid = d["cid"]  # 默认用第一个分P
    part_name = ""
    if pages and page_num is not None:
        if 1 <= page_num <= len(pages):
            page = pages[page_num - 1]
            cid = page["cid"]
            part_name = page.get("part", f"P{page_num}")
        else:
            raise BilibiliError(f"分P参数 ?p={page_num} 超出范围（该视频共 {len(pages)} 个分P）")

    # 分P名称加入标题（仅多分P视频才加，避免单分P时标题重复）
    title = d["title"]
    if part_name and len(pages) > 1:
        title = f"{title} - {part_name}"

    info = BilibiliVideoInfo(
        bvid=bvid,
        aid=d["aid"],
        cid=cid,
        title=title,
        description=d.get("desc", ""),
        owner_name=d["owner"]["name"],
        duration=d.get("duration", 0),
        chapters=[
            Chapter(time=float(vp["from"]), title=vp["content"])
            for vp in d.get("view_points", [])
        ],
    )

    # ② 字幕列表(带 SESSDATA 才能拿到大部分视频的 CC/AI 字幕)
    # B 站 player/v2 API 有缓存不一致问题：不同请求可能命中不同 CDN 节点，
    # 返回不同版本的字幕数据（有时只有 1 条错误轨道，有时有 8 条正确轨道）。
    # 如果第一次只拿到 ≤1 条轨道，隔 2 秒重试一次。
    player_url = f"https://api.bilibili.com/x/player/v2?cid={cid}&aid={info.aid}"
    player = _api_get(player_url, sessdata=sessdata)
    if player.get("code") != 0:
        raise BilibiliError(f"播放器信息获取失败:{player.get('message', '未知错误')}")
    subs = player.get("data", {}).get("subtitle", {}).get("subtitles", [])

    if len(subs) <= 1:
        time.sleep(2)  # 等 CDN 缓存刷新
        player_retry = _api_get(player_url, sessdata=sessdata)
        if player_retry.get("code") == 0:
            subs_retry = player_retry.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if len(subs_retry) > len(subs):
                subs = subs_retry  # 重试拿到了更多轨道，用重试结果

    if not subs:
        hint = "可在 B 站开启 AI 字幕后重试" if not sessdata else "SESSDATA 可能已过期,请检查 .env 配置"
        raise BilibiliError(f"该视频没有字幕(CC 字幕或 AI 字幕),暂无法导入。{hint}")

    # 优先中文字幕,否则取第一个
    sub = next((s for s in subs if "zh" in s.get("lan", "")), subs[0])
    sub_url = sub["subtitle_url"]

    # ③ 字幕内容
    segments = _fetch_subtitle_raw(sub_url)
    if not segments:
        raise BilibiliError("字幕内容为空")

    markdown = _build_markdown(info, segments, url)
    return BilibiliResult(info=info, markdown=markdown)


def _build_markdown(info: BilibiliVideoInfo, segments: list[dict], url: str) -> str:
    """把视频信息 + 字幕片段拼成可读 markdown。"""
    parts: list[str] = [f"# {info.title}\n"]

    # 元数据(blockquote)
    meta_parts = [f"UP主: {info.owner_name}", f"时长: {_format_duration(info.duration)}"]
    parts.append(f"> {' · '.join(meta_parts)} · [B站原链接]({url})\n")
    if info.description:
        parts.append(f"> {info.description[:200]}\n")

    # 字幕正文
    if info.chapters:
        for i, ch in enumerate(info.chapters):
            parts.append(f"\n## {ch.title}\n")
            end_time = info.chapters[i + 1].time if i + 1 < len(info.chapters) else float("inf")
            chapter_segs = [s for s in segments if ch.time <= float(s.get("from", 0)) < end_time]
            text = _segments_to_paragraphs(chapter_segs)
            if text:
                parts.append(text)
    else:
        text = _segments_to_paragraphs(segments)
        if text:
            parts.append("\n" + text)

    return "\n".join(parts)


def _segments_to_paragraphs(segments: list[dict]) -> str:
    """把字幕片段合并成可读段落。

    - 连续片段直接拼接(去掉每条的尾随空格);
    - 遇到句末标点(。！？)或时间间隔 >3 秒 → 断段;
    - 空内容片段跳过。

    避免一字幕一行(太碎,chunker 不好切),合段后每段约 100–300 字,正好落在切块目标区间。
    """
    paragraphs: list[str] = []
    current: list[str] = []
    last_to = 0.0
    for seg in segments:
        text = (seg.get("content") or "").strip()
        if not text:
            continue
        seg_from = float(seg.get("from", 0))
        seg_to = float(seg.get("to", 0))
        # 时间间隔大 → 新段落
        if current and seg_from - last_to > 3.0:
            paragraphs.append("".join(current))
            current = []
        current.append(text)
        last_to = seg_to
        # 句末标点 → 段落结束
        if text.endswith(("。", "！", "？", "!", "?")):
            paragraphs.append("".join(current))
            current = []
    if current:
        paragraphs.append("".join(current))
    return "\n\n".join(p.strip() for p in paragraphs if p.strip())


def _format_duration(seconds: int) -> str:
    """秒 → ``mm:ss`` 或 ``h:mm:ss``。"""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def sanitize_filename(title: str) -> str:
    """视频标题 → 安全文件名(Windows 非法字符替换为 _，截断 100 字)。"""
    safe = re.sub(r'[<>:"/\\|?*]', "_", title).strip()
    return safe[:100] if safe else "bilibili_video"


__all__ = [
    "BilibiliError",
    "BilibiliResult",
    "BilibiliVideoInfo",
    "Chapter",
    "extract_bvid",
    "extract_page",
    "fetch_bilibili",
    "sanitize_filename",
]
