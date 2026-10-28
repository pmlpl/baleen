"""一次性:按 queries.csv 生成人工复核单(带真实父块正文),给主人只看不翻库。用完即删。"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from sqlalchemy import select

from app.core.db import session_factory
from app.models import Chunk, Note

ROOT = Path(__file__).resolve().parents[2]
QUERIES = ROOT / "eval" / "queries" / "queries.csv"
OUT = ROOT / "eval" / "queries" / "复核单.md"

EXCERPT = 320  # 每节最多展示多少字


def _tail(segment: str) -> str:
    """期望路径形如「笔记标题 > a > b」,取掉笔记标题,只留 a > b。"""
    parts = [p.strip() for p in segment.split(">")]
    return " > ".join(parts[1:]) if len(parts) > 1 else parts[0]


async def main() -> None:
    rows = list(csv.DictReader(QUERIES.open(encoding="utf-8-sig")))

    async with session_factory() as session:
        notes = {n.id: n.title for n in (await session.scalars(select(Note))).all()}
        title_to_id = {v: k for k, v in notes.items()}
        parents = (await session.scalars(select(Chunk).where(Chunk.is_parent.is_(True)))).all()

    by_note: dict[int, list[Chunk]] = {}
    for p in parents:
        by_note.setdefault(p.note_id, []).append(p)

    out: list[str] = [
        "# 20 条 query 人工复核单",
        "",
        "> 每条只干一件事：**判断「我认为答案在的那一节」，是不是真的在那儿**。",
        "> `⭐` = 最该看的 6 条（跨节 3 + 长查询 3），其余可以抽查。",
        "> 判完把结论写在每条末尾的方框里，或者直接在 `queries.csv` 的 `note` 列加一句「人工复核：对/勉强/不对」。",
        "",
        "---",
        "",
    ]

    for r in rows:
        kind = r["kind"]
        star = " ⭐ **重点**" if kind in {"cross-section", "long"} else ""
        out.append(f"## {r['qid']} · `{kind}`{star}")
        out.append("")
        out.append(f"**问**：{r['query']}")
        out.append("")
        out.append(f"**意图**：{r['intent']}")
        out.append("")
        if r["expected_note_title"] == "NA":
            out.append("**我认为**：库里**没有**这条的答案（负例，期望检索返回空/不相关）。")
            out.append("")
            out.append("**请判**：☐ 库里确实没有　☐ 其实有，在：____")
            out.append("")
            out.append("---")
            out.append("")
            continue

        nid = title_to_id.get(r["expected_note_title"])
        out.append(f"**我认为答案在**：《{r['expected_note_title']}》(note #{nid})")
        out.append("")
        segments = [s.strip() for s in r["expected_heading_path"].split("||")]
        chunks = by_note.get(nid, [])
        for i, seg in enumerate(segments, start=1):
            tail = _tail(seg)
            matched = [c for c in chunks if (c.heading_path or "").endswith(tail)]
            label = f"{i}. `{tail}`" if len(segments) > 1 else f"`{tail}`"
            out.append(f"{label}")
            if not matched:
                out.append("")
                out.append("   > ⚠️ **脚本没在库里找到这一节** —— 期望路径写错了，或这一节被切块合并了。")
                out.append("")
                continue
            for c in matched[:1]:
                text = " ".join((c.text or "").split())
                body = text[:EXCERPT] + ("…" if len(text) > EXCERPT else "")
                out.append(f"   > {body}")
                out.append("")
        out.append("**请判**：☐ 对（答案确实在这）　☐ 位置勉强（勉强算命中）　☐ 不对（答案是别处 / 这条不该这么问）")
        out.append("")
        out.append("---")
        out.append("")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"已写出 {OUT}（{len(out)} 行）")


if __name__ == "__main__":
    asyncio.run(main())
