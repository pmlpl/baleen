"""视频内容提取：下载 → ffmpeg 提取音频 → faster-whisper 转写
→ PySceneDetect 场景检测抽帧 → DeepSeek-flash 视觉逐字转录 + 真嵌图。

不依赖平台 AI 字幕系统（已证实不可靠），直接从视频本身提取内容：
- 下载视频/音频（抖音 Playwright 免 cookie；B站 yt-dlp + 浏览器兜底）
- ffmpeg 提取音频（16kHz mono WAV）→ faster-whisper（ctranslate2）转写，带时间戳段
- **场景检测抽帧**（PySceneDetect ContentDetector threshold=27 HSV）替换固定间隔：
  每个场景中点抽 1 帧 + dHash 去重（同一幻灯片讲半天只留 1 帧）+ 上限 30 帧
- **每帧视觉逐字转录**：DeepSeek-flash 逐字转录屏幕代码（保留缩进/符号）+ 描述图/动画
- 帧字节经 ``store_media`` 内容寻址存库 → markdown 里插 ``![alt](baleen-image:N)`` 真嵌图
  （按 transcript 时间戳对齐到段；下游 G0/organizer 用 baleen-image:N 往返还原 image 块）
- 合并：whisper 转写（文字）+ 关键帧（嵌图 + 文字层）→ 富 markdown

⚠️ 较慢（3-5 分钟视频约需 1-3 分钟；长视频十几分钟可接受），异步后台执行。
"""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger(__name__)

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

# 场景检测抽帧参数（替换旧的固定间隔抽帧）
# ContentDetector threshold=27（HSV 差异，PySceneDetect 默认值）；min_scene_len 防闪烁
SCENE_THRESHOLD = 27.0
SCENE_MIN_LEN_S = 2.0  # 最短场景时长（秒）
# 关键帧上限（防止超长视频烧太多视觉调用；超限均匀采样，后续可换信息量打分 top-N）
MAX_SCENE_FRAMES = 30
# dHash 去重阈值（汉明距离 ≤ 此值视为近重复，同一幻灯片只留 1 帧）
DHASH_DEDUP_THRESHOLD = 5
# 算 dHash 前裁掉底部比例：视频字幕条常驻底部，字幕逐句变会让同一画面判成不同帧
DHASH_CROP_BOTTOM = 0.25
# faster-whisper 模型名（small：benchmark 实测比 base 正确率高——base 把「一致性」错写成
# 「移植性」且输出繁体，small 正确且简体；GPU 上 15x 实时，速度不是瓶颈。
# 中文需要多语言模型不是 .en）
WHISPER_MODEL = "small"


@dataclass
class FrameContent:
    """一个关键帧的提取结果（场景检测 + 视觉转录 + 真嵌图）。"""

    timestamp: float  # 秒（场景内采样点）
    alt: str  # 画面简短描述（当图片 alt）
    text_layer: str  # 逐字转录的屏幕代码/文字（保留缩进/符号；无则空）
    media_key: str  # store_media 存的帧字节 key（内容寻址）
    scene_idx: int = -1  # 来源场景序号（-1=无场景信息，不参与同场景择优）


@dataclass
class VideoContent:
    """视频提取结果。"""

    title: str
    description: str
    owner_name: str
    duration: int  # 秒
    transcript_segments: list[dict] = field(default_factory=list)  # [{start, end, text}]
    frame_contents: list[FrameContent] = field(default_factory=list)
    markdown: str = ""
    source: str = "video-extraction"  # 标识来源
    # 关键帧元信息（供下游 G0/organizer 把 baleen-image:N 还原成 image 块）：
    # [{"index": N, "media_key": str, "alt": str, "text_layer": str, "timestamp": float}]
    frame_images: list[dict] = field(default_factory=list)
    # 抽出的候选帧数（去重后、视觉筛选前）：>0 但 frame_images 空 = 全被筛除/失败,
    # worker 依此显式告警（2026-11-19 P1a）
    frame_candidates: int = 0


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


def download_video(
    url: str,
    *,
    sessdata: str = "",
    cookies_str: str = "",
    cookies_file: str = "",
    output_dir: Path | None = None,
) -> Path:
    """用 yt-dlp 下载视频（含音频），返回视频文件路径。

    B 站 CDN 不稳定，视频文件大，下载可能失败/超时。调用方应捕获异常并降级。
    cookies_str 用于抖音等需要 cookie 字符串的平台（B站用 sessdata）。
    cookies_file 是现成 Netscape cookie 文件路径（优先级最高，B站风控兜底收获的）。
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
    cookie = _resolve_cookie_file(
        sessdata=sessdata, cookies_str=cookies_str, cookies_file=cookies_file, output_dir=out_dir
    )
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


def download_audio(
    url: str,
    *,
    sessdata: str = "",
    cookies_str: str = "",
    cookies_file: str = "",
    output_dir: Path | None = None,
) -> Path:
    """用 yt-dlp 只下载音频（m4a），返回音频文件路径。

    音频文件小（~8MB/10分钟），下载快。whisper 只需要音频。
    cookies_str 用于抖音等需要 cookie 字符串的平台（B站用 sessdata）。
    cookies_file 是现成 Netscape cookie 文件路径（优先级最高，B站风控兜底收获的）。
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
    cookie = _resolve_cookie_file(
        sessdata=sessdata, cookies_str=cookies_str, cookies_file=cookies_file, output_dir=out_dir
    )
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
    *,
    sessdata: str = "",
    cookies_str: str = "",
    cookies_file: str = "",
    output_dir: Path | None = None,
) -> str | None:
    """统一 cookie 处理，优先级：cookies_file（现成 Netscape 文件，B站风控兜底收获的）
    > cookies_str（抖音 cookie 字符串）> sessdata（B站 SESSDATA）。

    返回 cookie 文件路径或 None（无 cookie）。
    """
    if cookies_file:
        from pathlib import Path as _P

        p = _P(cookies_file)
        if p.is_file():
            return str(p)
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


def detect_scenes(
    video_path: Path,
    *,
    threshold: float = SCENE_THRESHOLD,
    min_scene_len_s: float = SCENE_MIN_LEN_S,
) -> list[tuple[float, float]]:
    """PySceneDetect ContentDetector 检测场景切换，返回 [(start_s, end_s)]。

    threshold=27（HSV 差异，PySceneDetect 默认）；min_scene_len_s 防闪烁（短于该值的场景并入相邻）。
    视频无切换（单一场景）→ 返回 [(0, duration)]。
    """
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector

    video = open_video(str(video_path))
    sm = SceneManager()
    min_scene_len = max(1, int(min_scene_len_s * video.frame_rate))
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    sm.detect_scenes(video, show_progress=False)
    scenes = sm.get_scene_list()
    if not scenes:
        duration = video.duration.get_seconds() if video.duration else 0.0
        return [(0.0, duration)]
    return [(s[0].get_seconds(), s[1].get_seconds()) for s in scenes]


def _dhash(path: Path, size: int = 8, crop_bottom: float = 0.0) -> int:
    """dHash：图片缩成 (size+1)×size 灰度，比较相邻像素 → size*size bit 感知哈希。

    ``crop_bottom``：裁掉底部比例（视频字幕条常驻底部，字幕逐句变会让同一画面
    判成不同帧——实测裁 25% 后同一白板帧的汉明距离从 8 降到 1）。
    比 pHash 简单（无需 DCT），对「同一幻灯片」去重够用；Pillow 已装，无新依赖。
    """
    from PIL import Image

    with Image.open(path) as img:
        if crop_bottom > 0:
            w, h = img.size
            img = img.crop((0, 0, w, max(1, int(h * (1 - crop_bottom)))))
        gray = img.convert("L").resize((size + 1, size))
        pixels = list(gray.tobytes())  # L 模式每像素 1 字节；tobytes 替代 deprecated getdata
    bits = 0
    for row in range(size):
        for col in range(size):
            idx = row * (size + 1) + col
            if pixels[idx] > pixels[idx + 1]:
                bits |= 1 << (row * size + col)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _dedup_by_dhash(
    frames: list[tuple],
    *,
    threshold: int = DHASH_DEDUP_THRESHOLD,
    crop_bottom: float = DHASH_CROP_BOTTOM,
) -> list[tuple]:
    """近重复（汉明距离 ≤ threshold）只留第一个——同一幻灯片讲半天只留 1 帧。

    元组不定长（中点/75% 候选带场景序号，旧调用方是二元组）：只取 [0]=时间戳
    [1]=路径参与判定，整个元组原样保留。``crop_bottom`` 裁掉底部字幕条再算 hash：
    实际视频字幕逐句变但画面没变，不裁的话同一白板会被判成多帧（SpaceX 样本实测：
    帧1/帧2 距离 8→裁 25% 后 1）。
    """
    if len(frames) <= 1:
        return frames
    kept: list[tuple] = []
    hashes: list[int] = []
    for frame in frames:
        path = frame[1]
        try:
            h = _dhash(path, crop_bottom=crop_bottom)
        except Exception:  # noqa: BLE001 —— 读图失败按「独一无二」处理，不丢帧
            h = -1
        if any(h != -1 and _hamming(h, h2) <= threshold for h2 in hashes):
            continue
        hashes.append(h)
        kept.append(frame)
    return kept


def extract_scene_frames(
    video_path: Path,
    scenes: list[tuple[float, float]],
    output_dir: Path,
    *,
    max_frames: int = MAX_SCENE_FRAMES,
) -> list[tuple[float, Path, int]]:
    """每场景抽中点+75%+95% 三个候选（ffmpeg -ss seek）+ dHash 去重 + 超上限均匀采样。

    多候选（2026-11-19 复核批起步双候选，当晚复核扩三点）：场景中点常截在动画淡入
    淡出过程上（豆包淡帧/NestJS 错位帧）；75% 点对慢速逐字/打字机动画仍不够——
    6096 财富自由公式帧截在「下划线占位+零散加号」的半截状态（主人复核实锤）。
    95% 点几乎必然在逐字动画完成后；同场景多候选都活过视觉筛选时，
    ``transcribe_frames`` 末尾同场景择优只留时间靠后（画面最稳定）的一帧。
    静止场景多候选 dHash 近似会自动去重回 1 帧，不涨视觉成本。
    """
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    result: list[tuple[float, Path, int]] = []
    for i, (start, end) in enumerate(scenes):
        span = end - start
        for j, ts in enumerate(((start + end) / 2, start + span * 0.75, start + span * 0.95)):
            frame_path = frames_dir / f"frame_{i:04d}_{j}.jpg"
            # -ss 放在 -i 前是 fast seek（跳到最近 keyframe），单帧抽取足够快且准
            subprocess.run(
                [
                    "ffmpeg", "-ss", f"{ts:.3f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "2", "-y", str(frame_path),
                ],
                capture_output=True,
                check=True,
            )
            if frame_path.is_file():
                result.append((ts, frame_path, i))

    result = _dedup_by_dhash(result)
    if len(result) > max_frames:
        # 超上限均匀采样（样本阶段；后续可换视觉模型评信息量取 top-N）
        step = len(result) / max_frames
        result = [result[int(i * step)] for i in range(max_frames)]
    return result


def _parse_frame_output(content: str) -> tuple[str, str, bool]:
    """解析视觉模型输出 → (text_layer, desc, is_informative)。

    约定输出格式：
        INFO: high|low
        TEXT_LAYER:
        <逐字转录的文字，没有则「无」>

        DESC:
        <简短画面描述>
    ``is_informative``：画面是否值得嵌进笔记（有实质文字/图表/代码/界面 = high；
    表情包/黑屏/纯标题卡/转场动画 = low）。解析不出 INFO 时**保守当 informative=True**
    （宁留勿删，避免漏掉信息帧）。
    """
    import re

    text = content.strip()
    text_layer = ""
    desc = text  # 兜底：整体当描述
    is_informative = True  # 默认保守：判定不出就当有信息

    info_m = re.search(r"INFO\s*[:：]\s*(high|low)", text, re.IGNORECASE)
    if info_m:
        is_informative = info_m.group(1).lower() == "high"

    m = re.search(r"TEXT_LAYER\s*[:：]\s*(.*?)(?:\n\s*DESC\s*[:：]|$)", text, re.DOTALL | re.IGNORECASE)
    if m:
        text_layer = m.group(1).strip()
        m2 = re.search(r"DESC\s*[:：]\s*(.*)", text, re.DOTALL | re.IGNORECASE)
        desc = m2.group(1).strip() if m2 else ""
    if text_layer.lower() in ("无", "none", "（无）", "(无)", ""):
        text_layer = ""
    return text_layer, desc, is_informative


# 视觉逐字转录 prompt：逐字转录屏幕代码 + 知识相关性判定（2026-11-19 从「信息密度」升级：
# 原标准判「画面有没有字」——搜索框/互动引导页/主播口播帧因为有字幕文本全被判 high 混进
# 笔记（6009 一箭三连帧/6011 点赞收藏帧/6015 主播字幕帧实证）；且讲解动画的美术帧被
# 「转场动画」误伤整类丢光（6014 零图实证））
_FRAME_TRANSCRIBE_PROMPT = (
    "这是视频的一个关键画面截图。请做三件事：\n"
    "1. 【知识相关性】判断这个画面该不该放进笔记（标准是「画面是否承载正文讲解的"
    "知识内容」，不是「画面里有没有字」）：\n"
    "   - high：画面承载知识——代码、命令行、幻灯片要点、表格、图表、架构图、流程示意、"
    "软件操作/演示界面、讲解动画的完整关键帧（有图形结构或文字标注的动画画面）\n"
    "   - low：与知识无关——互动引导画面（点赞/收藏/关注/一键三连/评论区）、搜索框/"
    "地址栏自动补全、片头片尾包装、纯人物口播的特写（**即使画面有字幕，字幕内容口播里"
    "已经说了**）、转场动画的过渡帧、表情包/梗图、纯黑屏/纯标题卡\n"
    "2. 【逐字转录】画面里如果有代码/命令行/终端/幻灯片文字/表格，逐字转录出来，"
    "严格保留缩进、符号、大小写、表格结构，不要改写或修正，不要添加解释。"
    "是代码就用代码块包裹（标注语言，如 ```python）；表格用 markdown 表格；是纯文字就直接给。\n"
    "3. 【画面描述】用一句话描述这个画面展示什么（图表/架构图/动画的某一帧/纯人物说话/页面截图）。\n\n"
    "严格按以下格式输出（不要加额外说明）：\n"
    "INFO: high 或 low\n\n"
    "TEXT_LAYER:\n<转录的文字；画面里没有可读文字就写「无」>\n\n"
    "DESC:\n<一句话画面描述>"
)

# 解析失败时的兜底重试 prompt：只要描述（保证至少有 alt，不再产出「画面截图」空壳）
_FRAME_DESC_ONLY_PROMPT = (
    "这是视频的一个关键画面截图。请用一句话准确描述画面展示的内容："
    "是纯人物说话、还是图表/曲线图（说明图表讲什么）、架构图（说明结构）、"
    "软件界面截图（说明是什么软件和页面）、幻灯片（说明标题）、白板（说明画了什么）。"
    "只输出这一句话，不要解释。"
)


def transcribe_frames(
    frames: list[tuple],
    *,
    client,
    deadline_s: float = 600,
) -> list[FrameContent]:
    """DeepSeek-flash 视觉：逐字转录屏幕代码 + 知识相关性判定 + 描述画面；帧字节 store_media 存库。

    **知识相关性筛选**（2026-11-19 从「信息密度」升级）：模型判 INFO=low 的帧直接丢弃，
    不存 media、不进笔记。判定标准是「画面是否承载知识」，不是「有没有字」——原标准下
    搜索框/互动引导/主播口播帧靠字幕文本豁免混进笔记，讲解动画帧反被整类误杀。
    ⚠️ 同场景双候选（中点+75%）都活过筛选时只留时间靠后的一帧：中点常截在淡入淡出上，
    后段画面已稳定（2026-11-19 复核批：豆包淡帧/NestJS 错位帧）。
    ⚠️ 带总时限 deadline_s：LLM 网关挂起时单帧最多等满 client 超时，N 帧×超时=假死，
    超过总时限放弃剩余帧（帧是锦上添花，不能拖死导入）。
    ⚠️ 模型偶尔不守 TEXT_LAYER/DESC 格式（只回一句描述/废话）→ 兜底用纯描述 prompt
    重试一次，保证 alt 有真实画面描述而不是兜底的「画面截图」。
    """
    import time as _time

    from app.service.media import store_media

    results: list[FrameContent] = []
    started = _time.monotonic()
    kept = dropped = 0
    for frame in frames:
        timestamp, frame_path = frame[0], frame[1]
        scene_idx = frame[2] if len(frame) > 2 else -1
        if _time.monotonic() - started > deadline_s:
            break  # 总时限到，放弃剩余帧
        try:
            raw = frame_path.read_bytes()
            if len(raw) > 10 * 1024 * 1024:  # 超过 10MB 跳过
                continue
            b64 = base64.b64encode(raw).decode("ascii")
            image_part = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}

            chat = client.chat(
                [{"role": "user", "content": [{"type": "text", "text": _FRAME_TRANSCRIBE_PROMPT}, image_part]}],
                temperature=0.0,
                max_tokens=get_settings().llm_max_output_tokens,
            )
            text_layer, desc, is_informative = _parse_frame_output(chat.content)
            if not is_informative:
                # 与知识无关的帧 → 丢弃（不嵌进笔记，也不存 blob）。
                # 低信息帧上的字幕文本=口播内容,whisper 早已转写,保留零增益
                # （2026-11-19 前旧规则「low 且无 text_layer 才丢」放过了一箭三连/主播字幕帧）。
                dropped += 1
                continue
            if not desc and not text_layer:
                # 格式没被遵守且没解析出内容 → 兜底重试纯描述
                chat2 = client.chat(
                    [{"role": "user", "content": [{"type": "text", "text": _FRAME_DESC_ONLY_PROMPT}, image_part]}],
                    temperature=0.0,
                    max_tokens=get_settings().llm_max_output_tokens,
                )
                desc = chat2.content.strip()[:300]
            media_key = store_media(raw, "")  # 只给留下的信息帧存字节
            kept += 1
            results.append(
                FrameContent(
                    timestamp=timestamp, alt=desc or "画面截图", text_layer=text_layer,
                    media_key=media_key, scene_idx=scene_idx,
                )
            )
        except Exception:
            continue  # 单帧失败跳过（不丢其他帧）

    # 同场景双候选都存活 → 只留时间靠后的一帧（后段画面更可能已稳定）。
    # scene_idx=-1（无场景信息/旧调用方）不参与分组,原样保留。
    grouped = [fc for fc in results if fc.scene_idx >= 0]
    if grouped:
        by_scene: dict[int, FrameContent] = {}
        for fc in grouped:
            prev = by_scene.get(fc.scene_idx)
            if prev is None or fc.timestamp > prev.timestamp:
                by_scene[fc.scene_idx] = fc
        if len(by_scene) != len(grouped):
            results = (
                [fc for fc in results if fc.scene_idx < 0]
                + sorted(by_scene.values(), key=lambda f: f.timestamp)
            )
            log.info("同场景双候选择优：%d → %d 帧", len(grouped), len(by_scene))

    if dropped:
        log.info("帧知识相关性筛选：保留 %d / 丢弃 %d 张无关帧", kept, dropped)
    return results


def _extract_frames(video_path: Path | None, output_dir: Path, client) -> tuple[list[FrameContent], int]:
    """公共帧逻辑：场景检测双候选抽帧 + dHash 去重 + 视觉转录 + store_media 真嵌图。

    douyin / B站兜底 / yt-dlp 三条路径共用。返回 ``(存活的帧, 候选帧数)``——候选数
    供 worker 做「全部帧被筛除」的显式警告（6014 零图零警告教训：静默降级不可接受）。
    任何一步失败返回 ([], 0)，不阻塞转写。
    """
    if video_path is None:
        return [], 0
    try:
        scenes = detect_scenes(video_path)
        frames = extract_scene_frames(video_path, scenes, output_dir)
        if not frames:
            return [], 0
        return transcribe_frames(frames, client=client), len(frames)
    except Exception:  # noqa: BLE001 —— 帧提取失败不影响转写（与旧逻辑一致）
        return [], 0


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
    frame_contents: list[FrameContent],
    url: str,
) -> str:
    """合并 whisper 转写 + 关键帧（嵌图标记 + 文字层）→ 富 markdown。

    关键帧按时间戳对齐到 transcript 段（最近前置时间戳）：在对应字幕前插入
    帧的文字层（逐字转录的代码）+ ``![alt](baleen-image:N)`` 图片标记。
    N 是帧序号，下游 G0/organizer 用 ``frame_images[N]`` 的 media_key 还原成 image 块。
    """
    parts: list[str] = [f"# {title}\n"]

    # 元数据
    meta = [f"作者: {owner_name}", f"时长: {_format_timestamp(duration)}"]
    parts.append(f"> {' · '.join(meta)} · [原链接]({url})\n")
    if description:
        parts.append(f"> {description[:200]}\n")

    # 关键帧按时间戳对齐（int 秒做匹配键，与旧逻辑同款「最近前置时间戳」）
    frame_by_time = {int(fc.timestamp): (i, fc) for i, fc in enumerate(frame_contents)}

    current_time = 0.0
    for seg in transcript_segments:
        seg_start = seg["start"]
        # 在这段字幕前插入对应时间的帧（文字层 + 嵌图标记）
        for frame_time in list(frame_by_time.keys()):
            if frame_time <= seg_start and frame_time >= current_time:
                idx, fc = frame_by_time.pop(frame_time)
                _emit_frame(parts, idx, fc)
        parts.append(seg["text"])

    # 剩余帧（字幕结束后的画面）
    for frame_time in sorted(frame_by_time.keys()):
        idx, fc = frame_by_time[frame_time]
        _emit_frame(parts, idx, fc)

    return "\n".join(parts)


def _emit_frame(parts: list[str], idx: int, fc: FrameContent) -> None:
    """把一个关键帧的文字层（逐字转录）+ 嵌图标记追加到 parts。

    alt 带时间戳前缀 ``[mm:ss]``，方便审样本时核对图文对齐；organizer 重写时 alt 可改，
    但 ``baleen-image:N`` 标记必须保留（视频专用 prompt 约束）。
    """
    ts = _format_timestamp(fc.timestamp)
    if fc.text_layer:
        parts.append(f"\n{fc.text_layer}\n")
    parts.append(f"\n![[{ts}] {fc.alt}](baleen-image:{idx})\n")


def _fetch_video_info(url: str, *, sessdata: str = "", cookies_str: str = "", cookies_file: str = "") -> dict:
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


def _content_from_media(
    *,
    title: str,
    description: str,
    owner_name: str,
    duration_s: int,
    audio_path: Path,
    video_path: Path | None,
    url: str,
    client=None,
    progress_cb=None,
    output_dir: Path,
) -> VideoContent:
    """公共尾部：音频转写 + 场景抽帧 + 视觉转录 + 嵌图 markdown（douyin / B站兜底 / yt-dlp 共用）。

    调用方负责 output_dir 的创建与清理（finally rmtree）；帧字节已 ``store_media`` 持久化，
    output_dir 删除不影响已存的图片。
    """
    import shutil

    try:
        wav_path = extract_audio(audio_path, output_dir)
        if progress_cb:
            # est=视频时长是保守上限（faster-whisper 通常数倍于实时），前端据此插值画进度条
            progress_cb("whisper 转写中…", 30, est_seconds=duration_s)
        transcript_segments = transcribe_audio(wav_path)
        if progress_cb:
            progress_cb("识别画面中…", 72)

        frame_contents, frame_candidates = _extract_frames(video_path, output_dir, client)

        markdown = build_video_markdown(
            title=title,
            description=description,
            owner_name=owner_name,
            duration=duration_s,
            transcript_segments=transcript_segments,
            frame_contents=frame_contents,
            url=url,
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    return VideoContent(
        title=title,
        description=description,
        owner_name=owner_name,
        duration=duration_s,
        transcript_segments=transcript_segments,
        frame_contents=frame_contents,
        markdown=markdown,
        frame_candidates=frame_candidates,
        frame_images=[
            {
                "index": i,
                "media_key": fc.media_key,
                "alt": fc.alt,
                "text_layer": fc.text_layer,
                "timestamp": fc.timestamp,
            }
            for i, fc in enumerate(frame_contents)
        ],
    )


def _extract_douyin_content(url: str, *, client=None, progress_cb=None) -> VideoContent:
    """抖音专用路径：Playwright 拦截详情接口拿元信息+视频，免 cookie（2026-11-14）。

    视频文件在手 → 帧提取必定可用（比 yt-dlp 路径的「视频下载经常失败」更稳）。
    """
    from app.service.douyin_extractor import fetch_douyin_video

    output_dir = Path(tempfile.mkdtemp(prefix="douyin_extract_"))
    dv = fetch_douyin_video(
        url, output_dir=output_dir, max_duration_s=5400, progress_cb=progress_cb,
    )

    return _content_from_media(
        title=dv.title,
        description=dv.description,
        owner_name=dv.author,
        duration_s=dv.duration_s,
        audio_path=dv.video_path,  # douyin 下载的是完整视频（含音轨），ffmpeg 直接抽
        video_path=dv.video_path,
        url=url,
        client=client,
        progress_cb=progress_cb,
        output_dir=output_dir,
    )


def _extract_generic_content(
    url: str,
    *,
    sessdata: str = "",
    cookies_str: str = "",
    cookies_file: str = "",
    client=None,
    progress_cb=None,
) -> VideoContent:
    """通用 yt-dlp 路径（B站/其他平台）：元信息 → 下载音频(+可选视频) → 公共尾部。

    复用 ``_content_from_media``（转写 + 场景抽帧 + 嵌图），与 douyin / B站兜底同构。
    """
    output_dir = Path(tempfile.mkdtemp(prefix="video_extract_"))

    # ① 获取视频元信息（yt-dlp --dump-json，平台无关）
    info = _fetch_video_info(url, sessdata=sessdata, cookies_str=cookies_str, cookies_file=cookies_file)
    title = info["title"]
    description = info["description"]
    owner_name = info["uploader"]
    duration = info["duration"]
    resolved_url = info["webpage_url"]

    # 时长检查：faster-whisper small GPU ~15x 实时后，瓶颈在下载——
    # 上限 90 分钟（worker timeout=7200（2 小时）够全程）。
    MAX_WHISPER_DURATION = 5400  # 90 分钟
    if duration > MAX_WHISPER_DURATION:
        minutes = duration // 60
        raise RuntimeError(
            f"视频时长 {minutes} 分钟，超过 whisper 模式 60 分钟上限。"
            "whisper 转写长视频需要较长时间，请选择较短的视频。"
        )

    # ② 下载音频（总是执行——小文件、可靠）
    audio_file = download_audio(
        resolved_url,
        sessdata=sessdata, cookies_str=cookies_str, cookies_file=cookies_file,
        output_dir=output_dir,
    )
    # ③ 尝试下载视频（可选，帧提取用；失败跳过，不影响转写）
    video_path = None
    if client is not None:
        try:
            video_path = download_video(
                resolved_url,
                sessdata=sessdata, cookies_str=cookies_str, cookies_file=cookies_file,
                output_dir=output_dir,
            )
        except Exception:
            pass  # 视频下载失败不影响音频转写

    # ④ 公共尾部：转写 + 场景抽帧 + 嵌图（finally rmtree output_dir）
    return _content_from_media(
        title=title,
        description=description,
        owner_name=owner_name,
        duration_s=duration,
        audio_path=audio_file,
        video_path=video_path,
        url=resolved_url,
        client=client,
        progress_cb=progress_cb,
        output_dir=output_dir,
    )


def extract_video_content(
    url: str,
    *,
    sessdata: str = "",
    cookies_str: str = "",
    client=None,
    progress_cb=None,
) -> VideoContent:
    """主入口：路由平台 → 提取 → whisper 转写 →（可选）帧 → markdown。

    - 抖音：Playwright 拦截详情接口，免 cookie（douyin_extractor.py）
    - B站/其他：yt-dlp；**B站风控(页面级 412)失败时自动兜底**——
      Playwright+SESSDATA 过一次挑战收获全套 cookie（bilibili_harvest.py），
      用收获的 cookie 重试 yt-dlp（wbi 签名 yt-dlp 自己算，管线零改动）

    sessdata 用于 B站（SESSDATA cookie）；cookies_str 用于抖音等需要 cookie 字符串的平台。
    progress_cb(stage, pct, est_seconds=None)：提取阶段进度回调（worker 写 DB 供前端进度条）。
    """
    # 用户可能粘贴整段分享文本（"8.48 复制打开抖音… https://v.douyin.com/xxx/ 复制此链接"），
    # 提取出真正的 URL 再交给提取器
    url = _extract_url(url)

    # 抖音走 Playwright 专用路径（免 cookie，2026-11-14）
    from app.service.douyin import detect_douyin

    if detect_douyin(url):
        return _extract_douyin_content(url, client=client, progress_cb=progress_cb)

    # B站链接：yt-dlp 失败（典型为页面级 412 风控）时走 cookie 收获兜底
    from app.service.bilibili import extract_bvid

    is_bilibili = extract_bvid(url) is not None

    first_err_msg: str | None = None
    try:
        return _extract_generic_content(
            url, sessdata=sessdata, cookies_str=cookies_str, client=client, progress_cb=progress_cb,
        )
    except Exception as exc:
        # 非 B站链接不兜底；时长超限是确定性错误，兜底重试也不会变。
        # ⚠️ except as xxx 的名字在块尾会被 Python 自动删除——要在块内先取出消息
        if not is_bilibili or "超过 whisper 模式" in str(exc):
            raise
        first_err_msg = f"{type(exc).__name__}: {exc}"

    # 风控兜底（2026-11-14 深夜）：Playwright + SESSDATA 过挑战，从页面里直接提取。
    # cookie 收获重试被否——B站对 TLS 指纹打分，浏览器 cookie 迁给 yt-dlp 仍概率性 412；
    # 浏览器内提取（INITIAL_STATE + __playinfo__ + CDN 直连）才是确定性的。
    from app.service.bilibili_extractor import fetch_bilibili_media

    try:
        media = fetch_bilibili_media(url, sessdata=sessdata)
    except Exception as browser_err:
        raise RuntimeError(
            f"{first_err_msg}；浏览器兜底也失败: {browser_err}"
        ) from browser_err

    if media.duration_s > 3600:
        raise RuntimeError(
            f"视频时长 {media.duration_s // 60} 分钟，超过 whisper 模式 60 分钟上限。"
        )

    return _content_from_media(
        title=media.title,
        description=media.description,
        owner_name=media.author,
        duration_s=media.duration_s,
        audio_path=media.audio_path,
        video_path=media.video_path,
        url=url,
        client=client,
        progress_cb=progress_cb,
        output_dir=Path(tempfile.mkdtemp(prefix="bilibili_extract_")),
    )


__all__ = [
    "FrameContent",
    "VideoContent",
    "build_video_markdown",
    "detect_scenes",
    "download_audio",
    "download_video",
    "extract_audio",
    "extract_scene_frames",
    "extract_video_content",
    "transcribe_audio",
    "transcribe_frames",
]
