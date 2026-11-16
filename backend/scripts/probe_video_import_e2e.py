"""真链路验证：3 分钟样本 md + 帧清单 sidecar → 真实 run_import_async 全链。

验证点：G0 读 sidecar → organizer 视频多模态 → note 入库（doc 含 image 块）
→ 切块/向量化 → 笔记详情可取出 images（前端 NoteView 渲染依据）。

用法： cd backend && .venv/Scripts/python.exe -I scripts/probe_video_import_e2e.py
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.core.db import session_factory  # noqa: E402
from app.repo import Repos  # noqa: E402
from app.service.imports import run_import_async  # noqa: E402
from app.service.imports.pipeline import VIDEO_FRAMES_SUFFIX  # noqa: E402
from app.service.llm import LLMClient  # noqa: E402
from app.service.llm.resolve import resolve_llm_budgets, resolve_llm_config  # noqa: E402
from app.service.media import store_media  # noqa: E402
from app.service.notes_edit import _iter_images, doc_to_markdown  # noqa: E402

ROOT = Path(r"C:\Users\MOM\Desktop\学习项目\智能笔记库\baleen")
SAMPLE_MD = ROOT / "sample_spacex_3min.md"
FRAMES = ROOT / "sample_frames"
STAGING = Path(r"C:\Users\MOM\Desktop\学习项目\智能笔记库\baleen\backend\storage\uploads\_probe_e2e")
FRAME_TS = [40.0, 106.0]


async def main() -> None:
    async with session_factory() as s:
        base_url, api_key, model = await resolve_llm_config(s, 1)
        prompt_budget, output_budget = await resolve_llm_budgets(s, 1)
    print(f"LLM: {base_url} / {model}", flush=True)
    client = LLMClient(base_url=base_url, api_key=api_key, model=model, timeout=600)

    # 暂存目录：md + sidecar（帧字节 store_media → 真实 storage/media）
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    md_path = STAGING / "spacex_3min_probe.md"
    md_path.write_text(SAMPLE_MD.read_text(encoding="utf-8"), encoding="utf-8")

    manifest = []
    for i, ts in enumerate(FRAME_TS):
        data = (FRAMES / f"frame_{i}.jpg").read_bytes()
        key = store_media(data, "")
        manifest.append(
            {"index": i, "media_key": key, "alt": f"关键帧{i + 1}", "text_layer": "", "timestamp": ts}
        )
    (STAGING / (md_path.name + VIDEO_FRAMES_SUFFIX)).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[1] 暂存就绪: {md_path.name} + sidecar ({len(manifest)} 帧)", flush=True)

    print("[2] 跑真实 run_import_async（G0→G1→organizer→note→chunk→embed）…", flush=True)
    result = await run_import_async(
        md_path,
        owner_id=1,
        client=client,
        prompt_budget=prompt_budget,
        output_budget=output_budget,
        worker="probe-e2e",
    )
    print(
        f"    结果: source={result.source_id} note={result.note_id} "
        f"parse={result.parse_status} g1={result.g1_verdict} g2={result.g2_verdict} "
        f"quality={result.quality} chunks={result.chunks_total} emb={result.embeddings}",
        flush=True,
    )
    if result.note_id is None:
        print("!! 没有建笔记（可能 G1/组织器 reject）", flush=True)
        return

    # 校验落库 doc：图片块数量 + media key + 标题 + 章节
    async with session_factory() as s:
        repos = Repos(s, 1)
        note = await repos.notes.get(result.note_id)
        imgs = _iter_images((note.doc or {}).get("content") or [])
        print(f"[3] 笔记标题: {note.title!r}", flush=True)
        print(f"    quality={note.quality} image 块={len(imgs)}", flush=True)
        for i, img in enumerate(imgs):
            a = img.get("attrs") or {}
            print(f"      图{i}: media={str(a.get('media'))[:10]}… alt={str(a.get('alt'))[:40]!r}", flush=True)

        headings = [
            "".join(n.get("text", "") for n in b.get("content") or [])
            for b in (note.doc or {}).get("content") or []
            if b.get("type") == "heading"
        ]
        print(f"    章节({len(headings)}): {headings}", flush=True)

        # media 文件是否真在盘上（前端 GET /api/media/{key} 的依据）
        from app.service.media import media_path

        for i, img in enumerate(imgs):
            key = (img.get("attrs") or {}).get("media")
            p = media_path(key) if key else None
            print(f"    图{i} 文件: {'OK ' + str(p.stat().st_size) + 'B' if p else '缺失!'}", flush=True)

        md_out = doc_to_markdown(note.doc)
        out = ROOT / "sample_spacex_e2e_note.md"
        out.write_text(md_out, encoding="utf-8")
        print(f"[4] 落库笔记序列化: {out}", flush=True)

    shutil.rmtree(STAGING, ignore_errors=True)
    print("\n完成。前端打开笔记库即可看到该笔记（真图应正常渲染）。", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
