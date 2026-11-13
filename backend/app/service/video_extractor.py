"""视频内容提取：yt-dlp 下载 → ffmpeg 提取音频/帧 → whisper 转写 → 视觉模型描述帧。

不依赖 B 站 AI 字幕系统（已证实不可靠），直接从视频本身提取内容。
- yt-dlp 下载视频（B 站需要 SESSDATA cookie）
- ffmpeg 提取音频（16kHz mono WAV，whisper 要求）+ 关键帧（每 60 秒一帧）
- whisper base 模型转写音频 → 带时间戳的文字段
- LLMClient 视觉能力描述每帧画面 → 补充字幕无法获取的视觉信息
- 合并：whisper 转写（文字）+ 帧描述（画面）→ 富 markdown

⚠️ 整个过程较慢（10 分钟视频约需 1-3 分钟下载+转写），适合异步/后台执行。
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings

# whisper 模型懒加载（第一次调用时加载，后续复用）
_whisper_model = None

# 关键帧提取间隔（秒）：每 60 秒取一帧，10 分钟视频 → 10 帧 → 10 次视觉调用
FRAME_INTERVAL = 60
# 帧图片上限（防止超长视频烧太多视觉调用）
MAX_FRAMES = 20
# whisper 模型名（base 平衡速度与精度；中文需要多语言模型不是 .en）
WHISPER_MODEL = "base"


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


def _get_whisper_model():
    """懒加载 whisper 模型（全局复用，避免每次调用都加载 139MB）。"""
    global _whisper_model
    if _whisper_model is None:
        import whisper

        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _whisper_model = whisper.load_model(WHISPER_MODEL, device=device)
    return _whisper_model


def download_video(url: str, *, sessdata: str = "", output_dir: Path | None = None) -> Path:
    """用 yt-dlp 下载视频（含音频），返回视频文件路径。

    B 站 CDN 不稳定，视频文件大，下载可能失败/超时。调用方应捕获异常并降级。
    """
    import sys

    out_dir = output_dir or Path(tempfile.mkdtemp(prefix="bilibili_video_"))
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
    if sessdata:
        cookie_file = _write_cookies(sessdata, out_dir)
        cmd.extend(["--cookies", str(cookie_file)])
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 视频下载失败: {result.stderr[-300:]}")

    candidates = sorted(out_dir.glob("video.*"), key=lambda f: f.stat().st_size, reverse=True)
    if not candidates:
        raise RuntimeError(f"下载完成但找不到视频文件: {out_dir}")
    return candidates[0]


def download_audio(url: str, *, sessdata: str = "", output_dir: Path | None = None) -> Path:
    """用 yt-dlp 只下载音频（m4a），返回音频文件路径。

    音频文件小（~8MB/10分钟），下载快。whisper 只需要音频。
    用 subprocess 调 yt-dlp（B 站 CDN 不稳定，subprocess 方式更健壮）。
    """
    import sys

    out_dir = output_dir or Path(tempfile.mkdtemp(prefix="bilibili_audio_"))
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
    if sessdata:
        cookie_file = _write_cookies(sessdata, out_dir)
        cmd.extend(["--cookies", str(cookie_file)])
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
    """用 whisper 转写音频，返回段列表 [{start, end, text}]。"""
    model = _get_whisper_model()
    result = model.transcribe(
        str(audio_path),
        language="zh",  # 强制中文（B 站视频主要是中文）
        verbose=False,
    )
    return [
        {"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
        for seg in result.get("segments", [])
        if seg.get("text", "").strip()
    ]


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
    meta = [f"UP主: {owner_name}", f"时长: {_format_timestamp(duration)}"]
    parts.append(f"> {' · '.join(meta)} · [B站原链接]({url})\n")
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


def extract_video_content(
    url: str,
    *,
    sessdata: str = "",
    client=None,
) -> VideoContent:
    """主入口：下载音频 → whisper 转写 → （可选）下载视频提取帧 → 拼 markdown。

    策略：音频优先（小文件、下载可靠），视频可选（CDN 不稳定，失败则跳过帧提取）。
    不依赖 B 站 AI 字幕系统，直接从音频转写——内容可靠。
    需要 yt-dlp + ffmpeg + whisper + LLMClient（视觉模型，可选）。
    """
    import shutil

    output_dir = Path(tempfile.mkdtemp(prefix="bilibili_extract_"))

    # ① 获取视频元信息
    from app.service.bilibili import BilibiliError, extract_bvid, _resolve_short_link, _api_get

    bvid = extract_bvid(url)
    resolved_url = url
    if not bvid:
        try:
            resolved_url = _resolve_short_link(url)
            bvid = extract_bvid(resolved_url)
        except Exception:
            pass

    title = "bilibili_video"
    description = ""
    owner_name = ""
    duration = 0

    if bvid:
        try:
            view = _api_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", sessdata=sessdata)
            if view.get("code") == 0:
                d = view["data"]
                title = d["title"]
                description = d.get("desc", "")
                owner_name = d["owner"]["name"]
                duration = d.get("duration", 0)
        except Exception:
            pass

    # 时长检查：whisper base 模型转写速度约 1x 实时（GPU），
    # 30 分钟视频 ≈ 30 分钟转写 ≈ 1800 秒，远超 API 超时。限制在 30 分钟以内。
    MAX_WHISPER_DURATION = 1800  # 30 分钟
    if duration > MAX_WHISPER_DURATION:
        minutes = duration // 60
        raise BilibiliError(
            f"视频时长 {minutes} 分钟，超过 whisper 模式 30 分钟上限。"
            "whisper 转写长视频需要几十分钟，请改用「字幕快速」模式，或选择较短的视频。"
        )

    # ② 下载音频（总是执行——小文件、可靠）
    audio_file = download_audio(resolved_url, sessdata=sessdata, output_dir=output_dir)
    wav_path = extract_audio(audio_file, output_dir)

    # ③ whisper 转写
    transcript_segments = transcribe_audio(wav_path)

    # ④ 尝试下载视频 → 提取帧 → 视觉描述（可选，失败跳过）
    frame_descriptions: list[FrameDescription] = []
    if client is not None:
        try:
            video_path = download_video(resolved_url, sessdata=sessdata, output_dir=output_dir)
            frames = extract_key_frames(video_path, output_dir)
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
