"""完整探针：SpaceX 前 3 分钟 → whisper + 场景抽帧 + DeepSeek 视觉转录 + 嵌图 markdown。

产出图文 markdown 给主人审：图位置 / 代码原样转录 / 图文对齐。

用法：
  cd backend && .venv/Scripts/python.exe -I scripts/probe_video_full.py
  （自动下载 SpaceX 切前 3 分钟；或传已有切片路径省下载：
   probe_video_full.py <clip.mp4>）
"""
import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
from app.core.config import get_settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.service.llm import LLMClient  # noqa: E402
from app.service.llm.resolve import resolve_llm_config  # noqa: E402
from app.service.video_extractor import (  # noqa: E402
    build_video_markdown,
    detect_scenes,
    extract_audio,
    extract_scene_frames,
    transcribe_audio,
    transcribe_frames,
)

URL = "https://v.douyin.com/EHX7Crewh-8/"
CLIP_SECONDS = 180


async def get_client() -> LLMClient:
    async with session_factory() as s:
        base_url, api_key, model = await resolve_llm_config(s, 1)
    if not api_key:
        print("NO_KEY: 设置页未配 DeepSeek key")
        sys.exit(1)
    print(f"LLM: {base_url} / {model} (key 已配)", flush=True)
    return LLMClient(
        base_url=base_url, api_key=api_key, model=model,
        timeout=get_settings().llm_long_task_timeout,
    )


def main() -> None:
    client = asyncio.run(get_client())
    work = Path(tempfile.mkdtemp(prefix="spacex_full_"))
    print(f"[1] 工作目录: {work}", flush=True)

    if len(sys.argv) > 1:
        clip = Path(sys.argv[1])
        title = clip.stem
        print(f"[2] 使用已有切片: {clip}", flush=True)
    else:
        from app.service.douyin_extractor import fetch_douyin_video

        t = time.time()
        print(f"[2] 下载 SpaceX 视频（{URL}）…", flush=True)
        dv = fetch_douyin_video(
            URL, output_dir=work, max_duration_s=5400,
            progress_cb=lambda stage, pct, est=None: print(f"    {stage} {pct}%", flush=True),
        )
        print(f"    下载完成: {dv.title} ({dv.duration_s}s), 耗时 {time.time()-t:.0f}s", flush=True)
        clip = work / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-i", str(dv.video_path), "-t", str(CLIP_SECONDS), "-c", "copy", "-y", str(clip)],
            check=True, capture_output=True,
        )
        print(f"    切片: 前 {CLIP_SECONDS}s -> {clip}", flush=True)
        title = dv.title

    # whisper 转写
    t = time.time()
    wav = extract_audio(clip, work)
    print("[3] whisper 转写中…", flush=True)
    segs = transcribe_audio(wav)
    print(f"    转写完成: {len(segs)} 段, 耗时 {time.time()-t:.0f}s", flush=True)

    # 场景抽帧
    t = time.time()
    scenes = detect_scenes(clip)
    print(f"[4] 场景检测: {len(scenes)} 个场景, 耗时 {time.time()-t:.0f}s", flush=True)
    for i, (s, e) in enumerate(scenes[:10]):
        print(f"    场景{i}: {s:.0f}s-{e:.0f}s ({e-s:.0f}s)", flush=True)
    frames = extract_scene_frames(clip, scenes, work)
    print(f"    抽帧: {len(frames)} 帧(去重后)", flush=True)

    # DeepSeek 视觉转录
    t = time.time()
    print(f"[5] DeepSeek 视觉转录 {len(frames)} 帧…", flush=True)
    fcs = transcribe_frames(frames, client=client)
    print(f"    转录完成: {len(fcs)} 帧, 耗时 {time.time()-t:.0f}s", flush=True)
    for fc in fcs:
        tl = fc.text_layer.replace("\n", "\\n")[:90]
        print(f"    [{fc.timestamp:.0f}s] alt={fc.alt[:50]!r} text={tl!r}", flush=True)

    # 嵌图 markdown
    md = build_video_markdown(
        title=title, description="", owner_name="SpaceX",
        duration=CLIP_SECONDS, transcript_segments=segs, frame_contents=fcs, url=URL,
    )
    out_md = work / "spacex_clip_note.md"
    out_md.write_text(md, encoding="utf-8")
    print(f"\n=== 产出图文 markdown: {out_md} ===", flush=True)
    print(f"字符数: {len(md)} | 嵌图标记数: {md.count('baleen-image:')}", flush=True)
    print(f"\n--- 全文 ---\n{md}", flush=True)


if __name__ == "__main__":
    main()
