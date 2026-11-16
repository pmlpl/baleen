"""探针：验证场景检测抽帧 + 嵌图链路（不调真 LLM，验证本地结构）。

用法： cd backend && .venv/Scripts/python.exe -I scripts/probe_video_scene.py <video_path>
合成测试视频（2 场景 180s）：
  ffmpeg -f lavfi -i "testsrc=size=640x480:rate=24:duration=90" \
         -f lavfi -i "smptebars=size=640x480:rate=24:duration=90" \
         -filter_complex "[0:v][1:v]concat=n=2:v=1" -c:v libx264 -pix_fmt yuv420p -y probe.mp4
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from app.service.video_extractor import (  # noqa: E402
    FrameContent,
    build_video_markdown,
    detect_scenes,
    extract_scene_frames,
)

video = Path(sys.argv[1])
out = Path(tempfile.mkdtemp(prefix="probe_frames_"))

scenes = detect_scenes(video)
print(f"场景数: {len(scenes)}")
for i, (s, e) in enumerate(scenes):
    print(f"  场景{i}: {s:.1f}s - {e:.1f}s (时长 {e - s:.0f}s)")

frames = extract_scene_frames(video, scenes, out)
print(f"\n抽帧数(去重后): {len(frames)}")
for ts, p in frames:
    print(f"  帧 {ts:.1f}s -> {p.name} ({p.stat().st_size}B)")

# mock FrameContent（不调 LLM，只验证 build_video_markdown 嵌图结构）
fcs = [
    FrameContent(timestamp=ts, alt=f"画面{i}", text_layer=f"```python\ncode_{i}()\n```", media_key=f"fake{i}")
    for i, (ts, _) in enumerate(frames)
]
segs = [
    {"start": 0.0, "end": 5.0, "text": "第一段字幕"},
    {"start": 95.0, "end": 100.0, "text": "第二段字幕（场景切换后）"},
]
md = build_video_markdown(
    title="探针视频", description="", owner_name="test", duration=180,
    transcript_segments=segs, frame_contents=fcs, url="https://example.com",
)
out_md = Path(tempfile.gettempdir()) / "probe_video_out.md"
out_md.write_text(md, encoding="utf-8")
print(f"\n--- 产出 markdown（写到 {out_md}）---")
print(md)
