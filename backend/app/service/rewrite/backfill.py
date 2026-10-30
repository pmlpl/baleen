"""存量笔记的标题结构回填:把导入时的「AI 整理标题」补到历史笔记上。

rewrite(Outline) 只挂在导入管线里(pipeline.py),存量笔记没赶上 —— 本模块对
notes.doc 直接跑同一套 plan_outline → number_headings → apply_outline,然后
删光旧 chunks、按新 doc 重新切块+向量化。由 backend/scripts/backfill_outline.py
调用;核心逻辑放这里(依赖全部可注入)以便用 db_scope + FakeTransport 测试。

口径与 pipeline.py:364-390 对齐:
- 幂等主闸 sources.parse_meta.headings_rewritten,省钱副闸 needs_rewrite;
- note.title 不动(rewrite 不覆盖标题)、sources.doc 不动(保持「sources=原始解析」);
- LLM 返回空计划 → 不打标记、不改库(坏模型/空返回=可重试的 no-op);
- 只动标题正文一个字不改;单篇失败 rollback 后继续下一篇,不中断整批。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note, Source
from app.repo import Repos
from app.service.chunk import chunk
from app.service.embed import EMBEDDING_MODEL, count_tokens
from app.service.llm import LLMClient
from app.service.rewrite.outline import (
    apply_outline,
    doc_stats,
    needs_rewrite,
    number_headings,
    plan_outline,
)

log = logging.getLogger(__name__)

EmbedFunc = Callable[[list[str]], Any]
TokenCounter = Callable[[str], int]
ResultSink = Callable[["BackfillOutcome"], None]

# 回填结果状态(脚本打印/断点落盘/测试断言都用)
REWRITTEN = "rewritten"  # 已整理并重建切块/向量
NO_SPECS = "no_specs"  # LLM 返回空计划 → 库未动,可重试
SKIPPED_DONE = "skipped_done"  # sources 已有 headings_rewritten 标记
SKIPPED_STRUCTURE = "skipped_structure"  # needs_rewrite 判定结构已好/不值得
FAILED = "failed"
# 算进 --limit 的状态:真正花 LLM/动库的才算处理量,纯跳过不算
PROCESSED_STATUSES = (REWRITTEN, NO_SPECS, FAILED)


@dataclass
class BackfillOutcome:
    """一篇笔记的回填结果(脚本打印 / jsonl 落盘 / 测试断言)。"""

    note_id: int
    title: str
    status: str
    headings_added: int = 0
    old_chunks: int = 0
    new_chunks: int = 0
    embedded: int = 0
    error: str | None = None

    def as_row(self) -> dict:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "status": self.status,
            "headings_added": self.headings_added,
            "old_chunks": self.old_chunks,
            "new_chunks": self.new_chunks,
            "embedded": self.embedded,
            "error": self.error,
        }


async def list_candidates(session: AsyncSession, owner_id: int) -> list[dict]:
    """dry-run 用:该 owner 全部 active 笔记 + 回填判定信息(不调 LLM 不写库)。"""
    rows = await session.execute(
        select(Note, Source)
        .join(Source, Note.source_id == Source.id, isouter=True)
        .where(Note.owner_id == owner_id, Note.status == "active")
        .order_by(Note.id)
    )
    out: list[dict] = []
    for note, source in rows.all():
        parse_meta = (source.parse_meta if source is not None else None) or {}
        out.append(
            {
                "note_id": note.id,
                "title": note.title,
                "already_done": bool(parse_meta.get("headings_rewritten")),
                "needs_rewrite": needs_rewrite(note.doc),
                **doc_stats(note.doc),
            }
        )
    return out


async def backfill_notes(
    session: AsyncSession,
    owner_id: int,
    *,
    client: LLMClient,
    embed_func: EmbedFunc,
    token_counter: TokenCounter | None = None,
    note_ids: list[int] | None = None,
    limit: int | None = None,
    on_result: ResultSink | None = None,
) -> list[BackfillOutcome]:
    """对该 owner 的候选笔记逐篇回填;每篇一个事务,失败只影响当篇。

    ``note_ids`` 限定范围(--resume 过滤后传入);``limit`` 只数真正花 LLM/动库的
    篇数(PROCESSED_STATUSES);``on_result`` 在每篇结束时立即回调(断点落盘用)。
    """
    repos = Repos(session, owner_id)
    stmt = (
        select(Note, Source)
        .join(Source, Note.source_id == Source.id, isouter=True)
        .where(Note.owner_id == owner_id, Note.status == "active")
        .order_by(Note.id)
    )
    if note_ids:
        stmt = stmt.where(Note.id.in_(note_ids))
    rows = (await session.execute(stmt)).all()

    outcomes: list[BackfillOutcome] = []
    processed = 0
    for note, source in rows:
        if limit is not None and processed >= limit:
            break
        outcome = await _backfill_one(
            session,
            repos,
            note,
            source,
            client=client,
            embed_func=embed_func,
            token_counter=token_counter,
        )
        outcomes.append(outcome)
        if on_result is not None:
            on_result(outcome)
        if outcome.status in PROCESSED_STATUSES:
            processed += 1
    return outcomes


async def _backfill_one(
    session: AsyncSession,
    repos: Repos,
    note: Note,
    source: Source | None,
    *,
    client: LLMClient,
    embed_func: EmbedFunc,
    token_counter: TokenCounter | None,
) -> BackfillOutcome:
    outcome = BackfillOutcome(note_id=note.id, title=note.title, status=REWRITTEN)
    try:
        parse_meta = dict((source.parse_meta if source is not None else None) or {})
        if parse_meta.get("headings_rewritten"):
            outcome.status = SKIPPED_DONE
            return outcome
        if not needs_rewrite(note.doc):
            outcome.status = SKIPPED_STRUCTURE
            return outcome

        # LLM 是同步阻塞调用,丢线程池(照 pipeline 口径)
        specs = await asyncio.to_thread(
            plan_outline, note.doc, title=note.title, client=client
        )
        if not specs:
            outcome.status = NO_SPECS
            return outcome
        new_doc = apply_outline(note.doc, number_headings(specs))
        outcome.headings_added = len(specs)

        # 换 doc + 重建切块 + 重嵌在同一个事务里:任何一步炸了整体回滚,笔记保持原样
        outcome.old_chunks = await repos.chunks.count_by_note(note.id)
        await repos.notes.update(note.id, doc=new_doc)
        await repos.chunks.delete_by_note(note.id)
        result = chunk(new_doc, token_counter=token_counter or count_tokens)
        inserted = await repos.chunks.bulk_insert(note.id, result)
        outcome.new_chunks = len(result.chunks)

        todo = [
            (row.id, c.text)
            for c, row in inserted
            if not (row.meta or {}).get("skipped_embed")
        ]
        if todo:
            vectors = embed_func([text for _, text in todo])
            rows = [
                (cid, EMBEDDING_MODEL, len(vec), [float(x) for x in vec])
                for (cid, _), vec in zip(todo, vectors, strict=True)
            ]
            await repos.embeddings.bulk_insert(rows)
        outcome.embedded = len(todo)

        if source is not None:
            parse_meta["headings_rewritten"] = True
            source.parse_meta = parse_meta
        await session.commit()
        # 日志读 outcome 而不是 note:rollback 会过期 ORM 实例,同步读属性会炸
        log.info(
            "回填完成 note=%s「%s」+%s 标题,chunks %s→%s,向量 %s",
            outcome.note_id,
            outcome.title,
            outcome.headings_added,
            outcome.old_chunks,
            outcome.new_chunks,
            outcome.embedded,
        )
        return outcome
    except Exception as exc:  # noqa: BLE001 —— 单篇失败不中断整批
        await session.rollback()
        outcome.status = FAILED
        outcome.error = f"{type(exc).__name__}: {exc}"
        log.warning("回填失败 note=%s「%s」:%s", outcome.note_id, outcome.title, exc)
        return outcome


__all__ = [
    "FAILED",
    "NO_SPECS",
    "PROCESSED_STATUSES",
    "REWRITTEN",
    "SKIPPED_DONE",
    "SKIPPED_STRUCTURE",
    "BackfillOutcome",
    "backfill_notes",
    "list_candidates",
]
