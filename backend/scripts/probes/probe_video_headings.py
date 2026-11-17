"""探针：验证 organizer 视频标题层级规则（不重导视频，用已有笔记 doc 重跑 organizer）。

用途：主人反馈「一、直接是视频主题（应算笔记名）、下一级直接 16 块太多」。
本探针把 note 5468 的 doc（含 30 张图）直接喂 organizer，看新视频规则下的标题结构。

用法： cd backend && .venv/Scripts/python.exe -I scripts/probes/probe_video_headings.py [note_id]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.service.llm import LLMClient  # noqa: E402
from app.service.llm.resolve import resolve_llm_budgets, resolve_llm_config  # noqa: E402
from app.service.notes_edit import _iter_images, doc_to_markdown  # noqa: E402
from app.service.parse.types import ParseResult, ParseStatus  # noqa: E402
from app.service.rewrite.organizer import organize_note  # noqa: E402

NOTE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 5468
ROOT = Path(r"C:\Users\MOM\Desktop\学习项目\智能笔记库\baleen")


def show_structure(doc: dict, label: str) -> None:
    content = (doc or {}).get("content") or []
    h1 = h2 = h3 = 0
    print(f"\n--- {label} ---")
    for b in content:
        if b.get("type") == "heading":
            lvl = (b.get("attrs") or {}).get("level", 1)
            txt = "".join(n.get("text", "") for n in b.get("content") or [])
            if lvl == 1:
                h1 += 1
            elif lvl == 2:
                h2 += 1
            elif lvl == 3:
                h3 += 1
            print("  " * (lvl - 1) + f"H{lvl}: {txt}")
    print(f"=== H1={h1} H2={h2} H3={h3} | 图片={len(_iter_images(content))}")


async def main() -> None:
    async with session_factory() as s:
        base_url, api_key, model = await resolve_llm_config(s, 1)
        prompt_budget, output_budget = await resolve_llm_budgets(s, 1)
        row = (
            await s.execute(
                text(
                    "select n.doc, s.parse_meta from notes n join sources s on s.id=n.source_id "
                    "where n.id=:i"
                ),
                {"i": NOTE_ID},
            )
        ).fetchone()
    doc, meta_raw = row
    import json

    meta = meta_raw if isinstance(meta_raw, dict) else json.loads(meta_raw or "{}")
    print(f"note {NOTE_ID} | video_frame_count={meta.get('video_frame_count')}")

    images = _iter_images((doc or {}).get("content") or [])
    video_images = [
        {
            "index": i,
            "media_key": (img.get("attrs") or {}).get("media", ""),
            "alt": (img.get("attrs") or {}).get("alt", ""),
            "text_layer": "",
            "timestamp": (img.get("attrs") or {}).get("timestamp"),
        }
        for i, img in enumerate(images)
    ]

    show_structure(doc, "改前（当前库里的结构）")

    client = LLMClient(base_url=base_url, api_key=api_key, model=model, timeout=get_settings().llm_long_task_timeout)
    pr = ParseResult(parse_status=ParseStatus.OK, blocks=doc, meta={})
    print("\n重跑 organizer（新视频标题规则）…", flush=True)
    result = organize_note(
        pr,
        filename="video.md",
        ext=".md",
        client=client,
        prompt_budget=prompt_budget,
        output_budget=output_budget,
        video_images=video_images,
    )
    print(
        f"organizable={result.organizable} quality={result.quality} "
        f"tokens={result.cost_tokens} degraded={result.meta.get('degraded')}",
        flush=True,
    )
    if result.blocks:
        show_structure(result.blocks, "改后（新规则产出）")
        out = ROOT / f"sample_5468_headings_new.md"
        out.write_text(doc_to_markdown(result.blocks), encoding="utf-8")
        print(f"\n产出: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
