"""阶段2 探针：对 SpaceX 3分钟样本跑 organizer 视频多模态，产出最终图文笔记。

用法： cd backend && .venv/Scripts/python.exe -I scripts/probe_video_organize.py
产出：
  1. organizer 原始输出 markdown（baleen-image:N 标记）→ baleen/sample_spacex_organizer.md
  2. 可预览版（标记替换为 sample_frames 相对路径）→ baleen/sample_spacex_organizer_preview.md
  3. 结构校验：image 块数 / 图位置 / 章节结构
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.core.config import get_settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.service.llm import LLMClient  # noqa: E402
from app.service.llm.resolve import resolve_llm_config  # noqa: E402
from app.service.media import store_media  # noqa: E402
from app.service.parse.md_parser import parse_md_text  # noqa: E402
from app.service.parse.types import ParseResult, ParseStatus  # noqa: E402
from app.service.rewrite.organizer import organize_note  # noqa: E402

ROOT = Path(r"C:\Users\MOM\Desktop\学习项目\智能笔记库\baleen")
SAMPLE_MD = ROOT / "sample_spacex_3min.md"
FRAMES_DIR = ROOT / "sample_frames"
FRAME_TS = [40.0, 106.0]  # 修复去重后保留的 2 帧（讲者 0:40 / 白板 1:46）


async def get_client() -> LLMClient:
    async with session_factory() as s:
        base_url, api_key, model = await resolve_llm_config(s, 1)
    print(f"LLM: {base_url} / {model}", flush=True)
    return LLMClient(
        base_url=base_url, api_key=api_key, model=model,
        timeout=get_settings().llm_long_task_timeout,
    )


def main() -> None:
    client = asyncio.run(get_client())
    md_text = SAMPLE_MD.read_text(encoding="utf-8")

    # 构造 video_images（帧字节按内容寻址再存一次 → 同 key）
    video_images = []
    for i, ts in enumerate(FRAME_TS):
        data = (FRAMES_DIR / f"frame_{i}.jpg").read_bytes()
        key = store_media(data, "")
        video_images.append(
            {"index": i, "media_key": key, "alt": f"关键帧{i + 1}", "text_layer": "", "timestamp": ts}
        )
    print(f"帧清单: {len(video_images)} 帧, keys={[v['media_key'][:8] for v in video_images]}", flush=True)

    # G0：按视频 resolver 解析（baleen-image:N → image 块）
    from app.service.imports.pipeline import _video_image_resolver

    blocks, chars = parse_md_text(md_text, image_resolver=_video_image_resolver(video_images))
    pr = ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": blocks},
        meta={"chars": chars, "engine": "video-markdown"},
    )
    print(f"G0 解析: {chars} 字符, image 块数={sum(1 for b in blocks if b.get('type') == 'image')}", flush=True)

    # organizer 视频多模态
    print("organizer 视频多模态整理中（喂 2 帧图 + 全转写）…", flush=True)
    result = organize_note(
        pr, filename="spacex_3min.md", ext=".md", client=client, video_images=video_images
    )
    print(
        f"结果: organizable={result.organizable} quality={result.quality} "
        f"tokens={result.cost_tokens} latency={result.latency_ms:.0f}ms "
        f"degraded={result.meta.get('degraded')}",
        flush=True,
    )
    print(f"reasons: {result.reasons}", flush=True)

    if not result.organizable or not result.blocks:
        print("!! 不可整理或无 blocks，结束", flush=True)
        return

    # 产出 1：organizer 输出 markdown（从 blocks 序列化，带 baleen-image:N）
    from app.service.notes_edit import _iter_images, doc_to_markdown

    out_md = doc_to_markdown(result.blocks)
    out_path = ROOT / "sample_spacex_organizer.md"
    out_path.write_text(out_md, encoding="utf-8")
    print(f"\n[产出1] organizer 输出: {out_path} ({len(out_md)} 字符)", flush=True)

    imgs = _iter_images(result.blocks["content"])
    print(f"image 块数: {len(imgs)} (期望 2)", flush=True)
    for i, img in enumerate(imgs):
        a = img.get("attrs") or {}
        print(f"  图{i}: media={str(a.get('media'))[:8]}… alt={a.get('alt', '')[:40]!r} ts={a.get('timestamp')}", flush=True)

    # 图位置校验：每张图前后的标题
    content = result.blocks["content"]
    cur_head = "(文首)"
    placements = []
    for node in content:
        if node.get("type") == "heading":
            cur_head = "".join(n.get("text", "") for n in node.get("content") or [])
        elif node.get("type") == "image":
            placements.append((cur_head, (node.get("attrs") or {}).get("alt", "")))
    print("图位置: " + (" | ".join(f'「{h}」→ {a[:20]}' for h, a in placements) or "(无图)"), flush=True)

    # 产出 2：可预览版（baleen-image:N → sample_frames/frame_N.jpg）
    preview = re.sub(r"baleen-image:(\d+)", lambda m: f"sample_frames/frame_{m.group(1)}.jpg", out_md)
    pv_path = ROOT / "sample_spacex_organizer_preview.md"
    pv_path.write_text(preview, encoding="utf-8")
    print(f"\n[产出2] 可预览版: {pv_path}", flush=True)

    print("\n--- 全文 ---", flush=True)
    # 避免 GBK 编码报错：写文件后提示，不直接 print 全文
    print(f"（{len(out_md)} 字符，请看上面两个文件）", flush=True)


if __name__ == "__main__":
    main()
