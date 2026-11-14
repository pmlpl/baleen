"""视频内容提取：yt-dlp 下载 → ffmpeg 提取音频/帧 → faster-whisper 转写 → 视觉模型描述帧。

不依赖 B 站 AI 字幕系统（已证实不可靠），直接从视频本身提取内容。
- yt-dlp 下载视频（B 站需要 SESSDATA cookie）
- ffmpeg 提取音频（16kHz mono WAV，whisper 要求）+ 关键帧（间隔按时长自适应，目标 ~8 帧）
- faster-whisper（ctranslate2）转写音频 → 带时间戳的文字段（GPU float16，失败退 CPU int8）
- LLMClient 视觉能力描述每帧画面 → 补充字幕无法获取的视觉信息
- 合并：whisper 转写（文字）+ 帧描述（画面）→ 富 markdown

⚠️ 整个过程较慢（10 分钟视频约需 1-3 分钟下载+转写），适合异步/后台执行。
"""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings

# 从分享文本提取 URL（用户常把整段分享文本直接粘贴进来）。
# 停在空白/中日韩字符/常见中文标点处——抖音短链后常直接跟「复制此链接」。
_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff，。；！？、\"'（）【】<>《》]+")


def _extract_url(text: str) -> str:
    """分享文本 → 真 URL。纯 URL 原样返回；找不到 URL 也原样返回（让 yt-dlp 报错）。"""
    if text.startswith(("http://", "https://")):
        return text
    m = _URL_RE.search(text)
    return m.group(0) if m else text

# whisper 模型懒加载（第一次调用时加载，后续复用）
_whisper_model = None
_whisper_device = ""  # 当前模型所在设备（"cuda"/"cpu"），GPU 推理失败退 CPU 后据此跳过重试

# 关键帧默认提取间隔（秒）：调用方按视频时长自适应计算后显式传参覆盖（见 _frame_interval）
FRAME_INTERVAL = 60
# 帧图片上限（防止超长视频烧太多视觉调用）
MAX_FRAMES = 20


def _frame_interval(duration_s: int) -> int:
    """按视频时长自适应帧间隔：目标 ~8 帧，下限 10s（60s 的短视频至少给 6 帧）。

    10s 视频 → 1 帧；60s → 6 帧；8 分钟 → 8 帧；25 分钟 → 8 帧。
    MAX_FRAMES 仍作为超长视频的兜底上限。
    """
    return max(10, duration_s // 8)
# faster-whisper 模型名（small：benchmark 实测比 base 正确率高——base 把「一致性」错写成
# 「移植性」且输出繁体，small 正确且简体；GPU 上 15x 实时，速度不是瓶颈。
# 中文需要多语言模型不是 .en）
WHISPER_MODEL = "small"


@dataclass
class FrameDescription:
    """一个关键帧的描述。"""

    timestamp: float  # 秒
    description: str


@dataclass
class VideoContent:
    """视频提取结果。"""

    title: str
    description: str
    owner_name: str
    duration: int  # 秒
    transcript_segments: list[dict] = field(default_factory=list)  # [{start, end, text}]
    frame_descriptions: list[FrameDescription] = field(default_factory=list)
    markdown: str = ""
    source: str = "video-extraction"  # 标识来源


def _load_whisper(device: str):
    """按设备构建 faster-whisper 模型。GPU 用 float16，CPU 用 int8。"""
    from faster_whisper import WhisperModel

    if device == "cuda":
        return WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


def _register_cuda_dll_dirs() -> None:
    """Windows: ctranslate2 动态加载 cublas64_12.dll/cudnn，但 DLL 在 torch/lib 里不在 PATH 上。

    先 import torch（把 CUDA 运行时加载进进程），再显式注册 torch/lib 为 DLL 搜索目录。
    两者都失败也不致命——WhisperModel 会再抛错，由上层降级到 CPU。
    """
    import os

    try:
        import torch

        lib_dir = Path(torch.__file__).parent / "lib"
        if lib_dir.exists():
            os.add_dll_directory(str(lib_dir))
    except Exception:  # noqa: BLE001 —— torch 都没有则 CUDA 必然不可用，交给上层降级
        pass


def _get_whisper_model():
    """懒加载 faster-whisper 模型（全局单例复用；先试 CUDA float16，失败退 CPU int8）。"""
    global _whisper_model, _whisper_device
    if _whisper_model is None:
        _register_cuda_dll_dirs()
        try:
            _whisper_model = _load_whisper("cuda")
            _whisper_device = "cuda"
        except Exception:  # noqa: BLE001 —— CUDA/cuDNN 不可用（无卡/驱动/DLL 缺失）退 CPU
            _whisper_model = _load_whisper("cpu")
            _whisper_device = "cpu"
    return _whisper_model


def download_video(url: str, *, sessdata: str = "", cookies_str: str = "", output_dir: Path | None = None) -> Path:
    """用 yt-dlp 下载视频（含音频），返回视频文件路径。

    B 站 CDN 不稳定，视频文件大，下载可能失败/超时。调用方应捕获异常并降级。
    cookies_str 用于抖音等需要 cookie 字符串的平台（B站用 sessdata）。
    """
    import sys

    out_dir = output_dir or Path(tempfile.mkdtemp(prefix="video_dl_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--proxy", "",
        "-f", "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "--no-warnings", "--no-playlist", "--quiet",
        "--retries", "3",
        "--socket-timeout", "30",
        "-o", output_template,
    ]
    cookie = _resolve_cookie_file(sessdata=sessdata, cookies_str=cookies_str, output_dir=out_dir)
    if cookie:
        cmd.extend(["--cookies", cookie])
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 视频下载失败: {result.stderr[-300:]}")

    candidates = sorted(out_dir.glob("video.*"), key=lambda f: f.stat().st_size, reverse=True)
    if not candidates:
        raise RuntimeError(f"下载完成但找不到视频文件: {out_dir}")
    return candidates[0]


def download_audio(url: str, *, sessdata: str = "", cookies_str: str = "", output_dir: Path | None = None) -> Path:
    """用 yt-dlp 只下载音频（m4a），返回音频文件路径。

    音频文件小（~8MB/10分钟），下载快。whisper 只需要音频。
    cookies_str 用于抖音等需要 cookie 字符串的平台（B站用 sessdata）。
    """
    import sys

    out_dir = output_dir or Path(tempfile.mkdtemp(prefix="audio_dl_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "audio.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--proxy", "",
        "-f", "bestaudio[ext=m4a]/best",
        "--no-warnings", "--no-playlist", "--quiet",
        "--retries", "5",
        "--socket-timeout", "30",
        "-o", output_template,
    ]
    cookie = _resolve_cookie_file(sessdata=sessdata, cookies_str=cookies_str, output_dir=out_dir)
    if cookie:
        cmd.extend(["--cookies", cookie])
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr[-500:]}")

    candidates = sorted(out_dir.glob("audio.*"), key=lambda f: f.stat().st_size, reverse=True)
    if not candidates:
        raise RuntimeError(f"下载完成但找不到音频文件: {out_dir}")
    return candidates[0]


def _write_cookies(sessdata: str, output_dir: Path) -> Path:
    """把 SESSDATA 写成 Netscape cookie 文件（yt-dlp 要求的格式）。"""
    cookie_file = output_dir / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t" + sessdata + "\n",
        encoding="utf-8",
    )
    return cookie_file


def _resolve_cookie_file(
    *, sessdata: str = "", cookies_str: str = "", output_dir: Path | None = None
) -> str | None:
    """统一 cookie 处理：cookies_str（抖音 cookie 字符串）优先于 sessdata（B站 SESSDATA）。

    返回 cookie 文件路径或 None（无 cookie）。
    """
    if cookies_str:
        out_dir = output_dir or Path(tempfile.mkdtemp(prefix="cookies_"))
        return str(_write_cookie_string(cookies_str, out_dir))
    if sessdata:
        out_dir = output_dir or Path(tempfile.mkdtemp(prefix="cookies_"))
        return str(_write_cookies(sessdata, out_dir))
    return None


def _write_cookie_string(cookies_str: str, output_dir: Path) -> Path:
    """把 cookie 字符串（key=value; key=value 格式，从浏览器 dev tools 复制）写成 Netscape cookie 文件。

    用于抖音等需要多个 cookie 的平台（ttwid/msToken/__ac_nonce 等）。
    兼容粘贴整个 Cookie 请求头（带 "Cookie:" 前缀）——自动剥掉前缀再解析。
    用户只粘贴单个裸值（如 ttwid 的 Value，没有 = 号）→ 默认当作 ttwid。
    """
    cookie_file = output_dir / "cookies.txt"
    text = cookies_str.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    # 裸值（无 = 号）→ 当作 ttwid 的值
    if "=" not in text:
        text = f"ttwid={text}"
    lines = ["# Netscape HTTP Cookie File\n"]
    for part in text.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if name and " " not in name:
            lines.append(f".douyin.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
    cookie_file.write_text("".join(lines), encoding="utf-8")
    return cookie_file


def extract_audio(video_path: Path, output_dir: Path) -> Path:
    """用 ffmpeg 从视频提取音频（16kHz mono WAV，whisper 要求）。"""
    audio_path = output_dir / "audio.wav"
    subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vn",  # 不要视频
            "-acodec", "pcm_s16le",
            "-ar", "16000",  # 16kHz
            "-ac", "1",  # 单声道
            "-y",  # 覆盖
            str(audio_path),
        ],
        capture_output=True,
        check=True,
    )
    return audio_path


def transcribe_audio(audio_path: Path) -> list[dict]:
    """用 faster-whisper 转写音频，返回段列表 [{start, end, text}]（下游拼 markdown 依赖此结构）。

    GPU 模型构造成功但推理时报错（Windows 上 cuDNN 版本不匹配的典型表现）→
    自动退 CPU int8 重建模型重跑一次；CPU 模型再报错则原样抛出。
    """
    global _whisper_model, _whisper_device

    def _run(model) -> list[dict]:
        segments_iter, _info = model.transcribe(
            str(audio_path),
            language="zh",  # 强制中文（视频来源主要是中文）
        )
        return [
            {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
            for seg in segments_iter
            if seg.text.strip()
        ]

    model = _get_whisper_model()
    try:
        return _run(model)
    except Exception:
        if _whisper_device != "cuda":
            raise
        _whisper_model = _load_whisper("cpu")
        _whisper_device = "cpu"
        return _run(_whisper_model)


def extract_key_frames(
    video_path: Path,
    output_dir: Path,
    *,
    interval: int = FRAME_INTERVAL,
    max_frames: int = MAX_FRAMES,
) -> list[tuple[float, Path]]:
    """用 ffmpeg 按间隔提取关键帧，返回 [(timestamp, frame_path)]。"""
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    pattern = str(frames_dir / "frame_%04d.jpg")

    # fps=1/interval 表示每 interval 秒取一帧
    subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", f"fps=1/{interval}",
            "-q:v", "2",  # JPEG 质量（2=高质量）
            "-y",
            pattern,
        ],
        capture_output=True,
        check=True,
    )

    frames = sorted(frames_dir.glob("frame_*.jpg"))
    # 文件名 frame_0001.jpg → timestamp = (index) * interval
    result = []
    for i, frame_path in enumerate(frames[:max_frames]):
        timestamp = i * interval
        result.append((timestamp, frame_path))
    return result


def describe_frames(
    frames: list[tuple[float, Path]],
    *,
    client,
) -> list[FrameDescription]:
    """用视觉模型描述每帧画面，返回 FrameDescription 列表。"""
    from app.service.llm.client import LLMError

    prompt = (
        "这是视频的一个画面截图。请描述画面中展示的内容，重点关注：\n"
        "- 代码/命令行/终端内容（如果有，尽量读出文字）\n"
        "- 图表/架构图/流程图（描述结构和关键信息）\n"
        "- 项目页面/网站截图（描述页面内容和功能）\n"
        "- 如果是纯人物说话的画面，简短描述即可\n"
        "只输出描述，不要解释。"
    )

    descriptions: list[FrameDescription] = []
    for timestamp, frame_path in frames:
        try:
            raw = frame_path.read_bytes()
            if len(raw) > 10 * 1024 * 1024:  # 超过 10MB 跳过
                continue
            b64 = base64.b64encode(raw).decode("ascii")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ]
            chat = client.chat(messages, temperature=0.0, max_tokens=get_settings().llm_max_output_tokens)
            desc = chat.content.strip()
            if desc:
                descriptions.append(FrameDescription(timestamp=timestamp, description=desc))
        except Exception:
            continue  # 单帧失败跳过

    return descriptions


def _format_timestamp(seconds: float) -> str:
    """秒 → mm:ss 或 h:mm:ss。"""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_video_markdown(
    *,
    title: str,
    description: str,
    owner_name: str,
    duration: int,
    transcript_segments: list[dict],
    frame_descriptions: list[FrameDescription],
    url: str,
) -> str:
    """合并 whisper 转写 + 帧描述 → 富 markdown。"""
    parts: list[str] = [f"# {title}\n"]

    # 元数据
    meta = [f"作者: {owner_name}", f"时长: {_format_timestamp(duration)}"]
    parts.append(f"> {' · '.join(meta)} · [原链接]({url})\n")
    if description:
        parts.append(f"> {description[:200]}\n")

    # 帧描述（按时间戳插入，标记为画面信息）
    frame_by_time = {int(fd.timestamp): fd for fd in frame_descriptions}

    # whisper 转写为主体
    current_time = 0.0
    for seg in transcript_segments:
        seg_start = seg["start"]
        # 在这段字幕之前插入对应时间的帧描述
        for frame_time in list(frame_by_time.keys()):
            if frame_time <= seg_start and frame_time >= current_time:
                fd = frame_by_time.pop(frame_time)
                parts.append(f"\n> 📷 [{_format_timestamp(fd.timestamp)}] 画面：{fd.description}\n")
        parts.append(seg["text"])

    # 剩余的帧描述（字幕结束后的）
    for frame_time in sorted(frame_by_time.keys()):
        fd = frame_by_time[frame_time]
        parts.append(f"\n> 📷 [{_format_timestamp(fd.timestamp)}] 画面：{fd.description}\n")

    return "\n".join(parts)


def _fetch_video_info(url: str, *, sessdata: str = "", cookies_str: str = "") -> dict:
    """用 yt-dlp 提取视频元信息（不下载，只取 JSON）。平台无关。

    返回 {title, duration, uploader, description, webpage_url}。
    """
    import json
    import sys

    output_dir = Path(tempfile.mkdtemp(prefix="video_info_"))
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json", "--skip-download",
        "--no-playlist", "--quiet",
        "--socket-timeout", "30",
    ]
    cookie = _resolve_cookie_file(sessdata=sessdata, cookies_str=cookies_str, output_dir=output_dir)
    if cookie:
        cmd.extend(["--cookies", cookie])
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 元信息提取失败: {result.stderr[-400:]}")

    info = json.loads(result.stdout)
    return {
        "title": info.get("title") or "video",
        "duration": int(info.get("duration") or 0),
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
        "description": info.get("description") or "",
        "webpage_url": info.get("webpage_url") or url,
    }


def _extract_douyin_content(url: str, *, client=None, progress_cb=None) -> VideoContent:
    """抖音专用路径：Playwright 拦截详情接口拿元信息+视频，免 cookie（2026-11-14）。

    视频文件在手 → 帧提取必定可用（比 yt-dlp 路径的「视频下载经常失败」更稳）。
    progress_cb(stage, pct, est_seconds)：阶段回调节 worker 写 DB（前端进度条用）。
    """
    import shutil

    from app.service.douyin_extractor import fetch_douyin_video

    output_dir = Path(tempfile.mkdtemp(prefix="douyin_extract_"))
    try:
        dv = fetch_douyin_video(url, output_dir=output_dir)

        # 时长检查（与主路径同一上限）
        if dv.duration_s > 3600:
            raise RuntimeError(
                f"视频时长 {dv.duration_s // 60} 分钟，超过 whisper 模式 60 分钟上限。"
            )

        wav_path = extract_audio(dv.video_path, output_dir)
        if progress_cb:
            # est=视频时长是保守上限（faster-whisper 通常数倍于实时），前端据此插值画进度条
            progress_cb("whisper 转写中…", 30, est_seconds=dv.duration_s)
        transcript_segments = transcribe_audio(wav_path)
        if progress_cb:
            progress_cb("识别画面中…", 72)

        frame_descriptions: list[FrameDescription] = []
        if client is not None:
            try:
                frames = extract_key_frames(dv.video_path, output_dir, interval=_frame_interval(dv.duration_s))
                if frames:
                    frame_descriptions = describe_frames(frames, client=client)
            except Exception:  # noqa: BLE001 —— 帧描述失败不影响转写
                pass

        markdown = build_video_markdown(
            title=dv.title,
            description=dv.description,
            owner_name=dv.author,
            duration=dv.duration_s,
            transcript_segments=transcript_segments,
            frame_descriptions=frame_descriptions,
            url=url,
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    return VideoContent(
        title=dv.title,
        description=dv.description,
        owner_name=dv.author,
        duration=dv.duration_s,
        transcript_segments=transcript_segments,
        frame_descriptions=frame_descriptions,
        markdown=markdown,
    )


def extract_video_content(
    url: str,
    *,
    sessdata: str = "",
    cookies_str: str = "",
    client=None,
    progress_cb=None,
) -> VideoContent:
    """主入口：下载音频 → whisper 转写 → （可选）下载视频提取帧 → 拼 markdown。

    策略：音频优先（小文件、下载可靠），视频可选（CDN 不稳定，失败则跳过帧提取）。
    不依赖平台字幕系统，直接从音频转写——内容可靠。
    需要 yt-dlp + ffmpeg + whisper + LLMClient（视觉模型，可选）。

    sessdata 用于 B站（SESSDATA cookie）；cookies_str 用于抖音等需要 cookie 字符串的平台。
    progress_cb(stage, pct, est_seconds=None)：提取阶段进度回调（worker 写 DB 供前端进度条）。
    """
    import shutil

    # 用户可能粘贴整段分享文本（"8.48 复制打开抖音… https://v.douyin.com/xxx/ 复制此链接"），
    # 提取出真正的 URL 再交给 yt-dlp
    url = _extract_url(url)

    # 抖音走 Playwright 专用路径（免 cookie，2026-11-14）
    from app.service.douyin import detect_douyin

    if detect_douyin(url):
        return _extract_douyin_content(url, client=client, progress_cb=progress_cb)

    output_dir = Path(tempfile.mkdtemp(prefix="video_extract_"))

    # ① 获取视频元信息（yt-dlp --dump-json，平台无关）
    info = _fetch_video_info(url, sessdata=sessdata, cookies_str=cookies_str)
    title = info["title"]
    description = info["description"]
    owner_name = info["uploader"]
    duration = info["duration"]
    resolved_url = info["webpage_url"]

    # 时长检查：60 分钟上限保留（GPU 上 faster-whisper 转写远快于实时，
    # 瓶颈主要是音频/视频下载），worker 的 timeout=7200（2 小时）够用。
    MAX_WHISPER_DURATION = 3600  # 60 分钟
    if duration > MAX_WHISPER_DURATION:
        minutes = duration // 60
        raise RuntimeError(
            f"视频时长 {minutes} 分钟，超过 whisper 模式 60 分钟上限。"
            "whisper 转写长视频需要较长时间，请选择较短的视频。"
        )

    # ② 下载音频（总是执行——小文件、可靠）
    audio_file = download_audio(resolved_url, sessdata=sessdata, cookies_str=cookies_str, output_dir=output_dir)
    wav_path = extract_audio(audio_file, output_dir)

    # ③ whisper 转写（est=时长为保守上限，实际 GPU 上通常数倍于实时）
    if progress_cb:
        progress_cb("whisper 转写中…", 30, est_seconds=duration)
    transcript_segments = transcribe_audio(wav_path)
    if progress_cb:
        progress_cb("识别画面中…", 72)

    # ④ 尝试下载视频 → 提取帧 → 视觉描述（可选，失败跳过）
    frame_descriptions: list[FrameDescription] = []
    if client is not None:
        try:
            video_path = download_video(resolved_url, sessdata=sessdata, cookies_str=cookies_str, output_dir=output_dir)
            frames = extract_key_frames(video_path, output_dir, interval=_frame_interval(duration))
            if frames:
                frame_descriptions = describe_frames(frames, client=client)
        except Exception:
            pass  # 视频下载失败不影响音频转写

    # ⑤ 合并 → markdown
    markdown = build_video_markdown(
        title=title,
        description=description,
        owner_name=owner_name,
        duration=duration,
        transcript_segments=transcript_segments,
        frame_descriptions=frame_descriptions,
        url=resolved_url,
    )

    # 清理临时文件
    shutil.rmtree(output_dir, ignore_errors=True)

    return VideoContent(
        title=title,
        description=description,
        owner_name=owner_name,
        duration=duration,
        transcript_segments=transcript_segments,
        frame_descriptions=frame_descriptions,
        markdown=markdown,
    )


__all__ = [
    "FrameDescription",
    "VideoContent",
    "build_video_markdown",
    "describe_frames",
    "download_audio",
    "download_video",
    "extract_audio",
    "extract_key_frames",
    "extract_video_content",
    "transcribe_audio",
]
