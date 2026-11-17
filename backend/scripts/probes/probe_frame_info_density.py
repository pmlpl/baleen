"""探针：对 note 5468 的 30 张已有帧逐张跑信息密度判定，看新筛选会留几张。

不重导视频，直接用 storage/media 里的帧字节 → DeepSeek 判 INFO high/low。
用途：验证「只留信息帧、丢装饰图」规则的实际效果。

用法： cd backend && .venv/Scripts/python.exe -I scripts/probes/probe_frame_info_density.py [note_id]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.service.llm import LLMClient  # noqa: E402
from app.service.llm.resolve import resolve_llm_config  # noqa: E402
from app.service.media import media_path  # noqa: E402
from app.service.notes_edit import _iter_images  # noqa: E402
from app.service.video_extractor import _parse_frame_output  # noqa: E402

import base64  # noqa: E402

NOTE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 5468
PROMPT = (
    "这是视频的一个关键画面截图。请做三件事：\n"
    "1. 【信息密度】判断这个画面值不值得放进笔记：\n"
    "   - high：画面有实质可读内容——代码、命令行、幻灯片要点、表格、图表、架构图、"
    "流程示意、软件界面（这些值得截图保留）\n"
    "   - low：画面几乎没有信息——表情包/梗图、纯黑屏/纯色背景、纯标题卡"
    "（只有视频标题或一句口号）、转场动画、纯人物说话的特写\n"
    "2. 【逐字转录】画面里如果有代码/命令行/终端/幻灯片文字/表格，逐字转录出来，"
    "严格保留缩进、符号、大小写、表格结构，不要改写或修正，不要添加解释。\n"
    "3. 【画面描述】用一句话描述这个画面展示什么。\n\n"
    "严格按以下格式输出：\nINFO: high 或 low\n\nTEXT_LAYER:\n<转录文字或「无」>\n\nDESC:\n<一句话描述>"
)


async def main() -> None:
    async with session_factory() as s:
        base_url, api_key, model = await resolve_llm_config(s, 1)
        row = (await s.execute(text("select doc from notes where id=:i"), {"i": NOTE_ID})).fetchone()
    imgs = _iter_images((row[0] or {}).get("content") or [])
    print(f"note {NOTE_ID}: {len(imgs)} 张图，逐张判信息密度…\n", flush=True)

    client = LLMClient(base_url=base_url, api_key=api_key, model=model, timeout=get_settings().llm_long_task_timeout)
    keep, drop = [], []
    for i, img in enumerate(imgs):
        a = img.get("attrs") or {}
        p = media_path(a.get("media"))
        if p is None:
            print(f"#{i:02d} media 缺失", flush=True)
            continue
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        chat = client.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            temperature=0.0, max_tokens=1200,
        )
        tl, desc, info = _parse_frame_output(chat.content)
        mark = "KEEP" if info else "DROP"
        (keep if info else drop).append(i)
        print(f"#{i:02d} [{mark}] alt={str(a.get('alt'))[:34]!r} | desc={desc[:44]!r}", flush=True)

    print(f"\n=== 保留 {len(keep)} / 丢弃 {len(drop)} ===", flush=True)
    print(f"保留: {keep}", flush=True)
    print(f"丢弃: {drop}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
