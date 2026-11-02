"""导入流水线(确定性状态机,可重放/可续跑)。

阶段:sha256 → 去重 → 存 blob → G0 → G1 →（视情况）G2 → 产 note → 切块 → 向量化 → 落库。
每个阶段结束 ``commit`` 并更新 ``jobs.status``;任何异常先记 ``jobs.last_error`` 再抛出。

⚠️ 同步门面 ``run_import`` 内部 ``asyncio.run``,不能在已运行的事件循环里调用;
arq worker 请 ``await run_import_async(...)`` 或 ``await asyncio.to_thread(run_import, ...)``。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import ROOT_DIR, get_settings
from app.repo import Repos
from app.service.chunk import ChunkResult, chunk
from app.service.embed import EMBEDDING_MODEL, count_tokens, embed_passage
from app.service.gate import judge as judge_g1
from app.service.gate.g2 import judge as judge_g2
from app.service.llm import LLMClient
from app.service.media import store_media
from app.service.parse import ParseResult, ParseStatus, parse_file
from app.service.parse.blocks import empty_doc
from app.service.rewrite.outline import (
    apply_outline,
    needs_rewrite,
    number_headings,
    plan_outline,
)

STORAGE_ROOT = ROOT_DIR / "storage"
DEDUPE_PREFIX = "import:"
WORKER = "import-cli"

log = logging.getLogger(__name__)

EmbedFunc = Callable[[list[str]], Any]
TokenCounter = Callable[[str], int]


@dataclass
class ImportResult:
    """一次导入的结果(CLI 打印 / 测试断言 / 后续 review 用)。"""

    source_id: int | None = None
    note_id: int | None = None
    deduped: bool = False
    dry_run: bool = False
    parse_status: str = ""
    g1_verdict: str | None = None
    g2_verdict: str | None = None
    quality: str | None = None
    action: str | None = None
    chunks_total: int = 0
    chunks_parents: int = 0
    chunks_children: int = 0
    embeddings: int = 0
    message: str | None = None
    # 标题结构二次加工:是否跑了、加了几条
    headings_rewritten: bool = False
    headings_added: int = 0
    stages: list[tuple[str, float]] = field(default_factory=list)

    def stage_ms(self, name: str) -> float:
        for key, ms in self.stages:
            if key == name:
                return ms
        return 0.0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store_blob(data: bytes, sha: str) -> Path:
    """内容寻址存储:``storage/{sha[:2]}/{sha}``(AGENTS §4)。已存在则复用。"""
    target = STORAGE_ROOT / sha[:2] / sha
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return target


def _title(doc: dict, filename: str) -> str:
    for block in doc.get("content") or []:
        if block.get("type") == "heading":
            text = "".join(n.get("text", "") for n in block.get("content") or []).strip()
            if text:
                return text
    return Path(filename).stem


def _from_source(source) -> ParseResult:
    """从 sources 行重建 ParseResult(断点续跑时不再解析原文件)。"""
    meta = dict(source.parse_meta or {})
    warnings = meta.pop("warnings", []) or []
    message = meta.pop("message", None)
    return ParseResult(
        parse_status=ParseStatus(source.parse_status),
        blocks=source.doc or empty_doc(),
        warnings=warnings,
        message=message,
        meta=meta,
    )


def _counts(rows: list) -> tuple[int, int, int]:
    parents = sum(1 for r in rows if r.is_parent)
    return len(rows), parents, len(rows) - parents


def _capture_info(
    result: ImportResult,
    path: Path,
    sha: str,
    owner_id: int,
    worker: str,
    note_id: int | None,
) -> dict[str, Any]:
    """组装候选归档参数(「使用即积累」);dedupe 路径不调用。"""
    return {
        "file_path": str(path),
        "sha256": sha,
        "parse_status": result.parse_status,
        "g1_verdict": result.g1_verdict,
        "g2_verdict": result.g2_verdict,
        "quality": result.quality,
        "note_id": note_id,
        "owner_id": owner_id,
        "via": f"import:{worker}",
    }


async def _finish(
    repos: Repos,
    job_id: int,
    result: ImportResult,
    note_id: int | None,
    session: AsyncSession,
    capture: dict[str, Any] | None = None,
) -> ImportResult:
    if note_id is not None:
        rows = await repos.chunks.get_by_note(note_id)
        result.chunks_total, result.chunks_parents, result.chunks_children = _counts(rows)
        result.embeddings = await repos.embeddings.count_by_note(note_id)
    await repos.jobs.update_status(
        job_id,
        "done",
        payload={
            "source_id": result.source_id,
            "note_id": note_id,
            "chunks": result.chunks_total,
            "embeddings": result.embeddings,
            "g1": result.g1_verdict,
            "g2": result.g2_verdict,
            "action": result.action,
        },
    )
    await session.commit()
    if capture is not None:
        # 「使用即积累」(主人 C 路线):真实导入(非 dedupe)完成后归档候选样本。
        # best-effort:失败只记日志,绝不动导入结果;同步 IO 丢线程不阻塞事件循环。
        from app.service.eval_capture import capture_candidate

        await asyncio.to_thread(capture_candidate, **capture)
    return result


async def run_import_async(
    file_path: str | Path,
    *,
    owner_id: int = 1,
    no_llm: bool = False,
    dry_run: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    charter: str | None = None,
    client: LLMClient | None = None,
    embed_func: EmbedFunc | None = None,
    token_counter: TokenCounter | None = None,
    worker: str = WORKER,
) -> ImportResult:
    """异步核心;所有外部依赖可注入(测试用假 embedder / 假 token 计数 / 隔离 session)。"""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在:{path}")
    stages: list[tuple[str, float]] = []
    result = ImportResult(stages=stages)

    def mark(name: str, started: float) -> None:
        stages.append((name, round((time.perf_counter() - started) * 1000, 1)))

    t = time.perf_counter()
    data = path.read_bytes()
    sha = _sha256(data)
    mark("sha256", t)

    if dry_run:  # 只走到 G1,不落库、不写 blob
        t = time.perf_counter()
        pr = parse_file(path)
        mark("G0", t)
        t = time.perf_counter()
        g1 = judge_g1(pr)
        mark("G1", t)
        result.dry_run = True
        result.parse_status = pr.parse_status.value
        result.g1_verdict = g1.verdict if pr.parse_status.value == "ok" else None
        result.quality = g1.quality if pr.parse_status.value == "ok" else None
        result.action = g1.action if pr.parse_status.value == "ok" else None
        result.message = pr.message
        return result

    factory = session_factory or _default_factory()
    dedupe_key = DEDUPE_PREFIX + sha
    job_id: int | None = None
    note_id: int | None = None

    async with factory() as session:
        repos = Repos(session, owner_id)
        try:
            # ---- 去重 + job + source ----
            t = time.perf_counter()
            job, _ = await repos.jobs.create_or_get(
                dedupe_key=dedupe_key,
                payload={"filename": path.name, "no_llm": no_llm},
            )
            job_id = job.id
            existing = await repos.sources.get_by_sha256(sha)
            if existing is not None and job.status == "done":
                result.deduped = True
                result.source_id = existing.id
                result.parse_status = existing.parse_status
                note = await repos.notes.get_by_source(existing.id)
                result.note_id = note.id if note else None
                g1_run = await repos.review_runs.get(existing.id, "G1")
                if g1_run is not None:
                    result.g1_verdict = g1_run.verdict
                    result.quality = g1_run.quality
                    result.action = g1_run.action
                g2_run = await repos.review_runs.get(existing.id, "G2")
                if g2_run is not None:
                    result.g2_verdict = g2_run.verdict
                    result.quality = g2_run.quality
                    result.action = g2_run.action
                await session.commit()
                mark("dedupe", t)
                return await _finish(repos, job_id, result, result.note_id, session)

            if existing is None:
                storage_path = _store_blob(data, sha)
                source, _ = await repos.sources.create_or_get_by_sha256(
                    sha256=sha,
                    filename=path.name,
                    ext=path.suffix.lower(),
                    size_bytes=len(data),
                    storage_path=str(storage_path),
                    mime=mimetypes.guess_type(path.name)[0],
                    parse_status="pending",  # 等 G0;解析完立刻改为真实状态
                )
                result.source_id = source.id
            else:
                result.source_id = existing.id
            await repos.jobs.claim(job_id, worker)
            await session.commit()
            mark("dedupe", t)
            source_id = result.source_id

            # ---- G0 解析 ----
            t = time.perf_counter()
            source = await repos.sources.get(source_id)
            g0_run = await repos.review_runs.get(source_id, "G0")
            if source.doc is not None and g0_run is not None:
                pr = _from_source(source)
            else:
                # blob 无扩展名(内容寻址),G0 按扩展名路由 → 解析原始路径(字节完全相同)。
                # client 随路下发:图片文件会走视觉转写(ADR 0006);no_llm 时不烧视觉。
                # md 里的本地图片经 media_sink 存字节(内容寻址),doc 只留 key(v16)
                pr = parse_file(path, client=None if no_llm else client, media_sink=store_media)
                await repos.sources.update_parse(
                    source_id,
                    parse_status=pr.parse_status.value,
                    parse_meta={
                        **(pr.meta or {}),
                        "warnings": pr.warnings,
                        "message": pr.message,
                    },
                    doc=pr.blocks,
                )
                if g0_run is None:
                    await repos.review_runs.add(
                        source_id=source_id,
                        gate="G0",
                        verdict="skip",
                        reasons=[*pr.warnings, *([pr.message] if pr.message else [])],
                    )
            result.parse_status = pr.parse_status.value
            result.message = pr.message
            await session.commit()
            mark("G0", t)

            if pr.parse_status.value != "ok":
                status = "failed" if pr.parse_status.value == "error" else "done"
                await repos.jobs.update_status(
                    job_id,
                    status,
                    last_error=pr.message if status == "failed" else None,
                    payload={"source_id": source_id, "parse_status": pr.parse_status.value},
                )
                await session.commit()
                return result

            # ---- G1 价值闸 ----
            t = time.perf_counter()
            g1_run = await repos.review_runs.get(source_id, "G1")
            if g1_run is not None:
                result.g1_verdict, result.quality, result.action = (
                    g1_run.verdict,
                    g1_run.quality,
                    g1_run.action,
                )
            else:
                g1 = judge_g1(pr)
                await repos.review_runs.add(
                    source_id=source_id,
                    gate="G1",
                    verdict=g1.verdict,
                    quality=g1.quality,
                    score=g1.score,
                    action=g1.action,
                    reasons=g1.reasons,
                )
                result.g1_verdict = g1.verdict
                result.quality = g1.quality
                result.action = g1.action
            await session.commit()
            mark("G1", t)

            if result.g1_verdict == "reject":
                return await _finish(
                    repos, job_id, result, None, session,
                    capture=_capture_info(result, path, sha, owner_id, worker, None),
                )

            # ---- G2 归属闸(可选)----
            if no_llm:
                result.g2_verdict = None
            else:
                t = time.perf_counter()
                g2_run = await repos.review_runs.get(source_id, "G2")
                if g2_run is not None:
                    result.g2_verdict, result.quality, result.action = (
                        g2_run.verdict,
                        g2_run.quality,
                        g2_run.action,
                    )
                else:
                    g2 = judge_g2(
                        pr,
                        charter if charter is not None else get_settings().resolve_charter(),
                        filename=path.name,
                        ext=path.suffix.lower(),
                        size_bytes=len(data),
                        client=client,
                    )
                    await repos.review_runs.add(
                        source_id=source_id,
                        gate="G2",
                        verdict=g2.verdict,
                        quality=g2.quality,
                        note_type=g2.note_type,
                        score=g2.score,
                        action=g2.action,
                        reasons=g2.reasons,
                        model=g2.model,
                        cost_tokens=g2.cost_tokens or 0,
                        latency_ms=int(round(g2.latency_ms)),
                    )
                    result.g2_verdict = g2.verdict
                    result.quality = g2.quality
                    result.action = g2.action
                await session.commit()
                mark("G2", t)
                if result.g2_verdict == "reject":
                    return await _finish(
                        repos, job_id, result, None, session,
                        capture=_capture_info(result, path, sha, owner_id, worker, None),
                    )

            # ---- 标题结构二次加工(可选)----
            # 放在建笔记之前:改的是 pr.blocks,同一份会流向 notes.doc 与 chunk(),
            # 一处修改两处生效。失败静默跳过(与 G2 降级同口径,不因整理失败而导入失败)。
            original_title = _title(pr.blocks, path.name)  # 先记下原标题,避免被重写的 H1 覆盖
            t = time.perf_counter()
            if (
                not no_llm
                and client is not None
                and not (source.parse_meta or {}).get("headings_rewritten")
                and needs_rewrite(pr.blocks)
            ):
                try:
                    specs = await asyncio.to_thread(
                        plan_outline, pr.blocks, title=original_title, client=client
                    )
                    if specs:
                        pr.blocks = apply_outline(pr.blocks, number_headings(specs))
                        result.headings_rewritten = True
                        result.headings_added = len(specs)
                        # 幂等标记:断点续跑时不再重复整理(重写结果幂等,但会白花钱)
                        meta = dict(source.parse_meta or {})
                        meta["headings_rewritten"] = True
                        source.parse_meta = meta
                        log.info("标题已整理: %s 条(%s)", len(specs), original_title)
                except Exception as exc:  # noqa: BLE001 —— 整理是锦上添花,静默跳过
                    log.warning("标题整理失败(不影响导入): %s", exc)
            mark("rewrite", t)

            # ---- note ----
            t = time.perf_counter()
            note = await repos.notes.get_by_source(source_id)
            if note is None:
                note = await repos.notes.create(
                    source_id=source_id,
                    title=original_title,
                    doc=pr.blocks,
                    quality=result.quality,
                )
            note_id = note.id
            result.note_id = note_id
            await session.commit()
            mark("note", t)

            # ---- 切块 ----
            t = time.perf_counter()
            child_rows = [
                (row.id, row.text, dict(row.meta or {}))
                for row in await repos.chunks.get_by_note(note_id)
                if not row.is_parent
            ]
            if not child_rows:
                chunk_result: ChunkResult = chunk(
                    pr.blocks, token_counter=token_counter or count_tokens
                )
                inserted = await repos.chunks.bulk_insert(note_id, chunk_result)
                child_rows = [(row.id, c.text, dict(c.meta or {})) for c, row in inserted]
            await session.commit()
            mark("chunk", t)

            # ---- 向量化(只子块;目录/图片 skipped_embed 跳过)----
            t = time.perf_counter()
            existing = await repos.embeddings.existing_chunk_ids(
                [cid for cid, _, _ in child_rows], EMBEDDING_MODEL
            )
            todo = [
                (cid, text)
                for cid, text, meta in child_rows
                if not meta.get("skipped_embed") and cid not in existing
            ]
            if todo:
                vectors = (embed_func or embed_passage)([text for _, text in todo])
                rows = [
                    (cid, EMBEDDING_MODEL, len(vec), [float(x) for x in vec])
                    for (cid, _), vec in zip(todo, vectors, strict=True)
                ]
                await repos.embeddings.bulk_insert(rows)
            await session.commit()
            mark("embed", t)

            return await _finish(
                repos, job_id, result, note_id, session,
                capture=_capture_info(result, path, sha, owner_id, worker, note_id),
            )

        except Exception as exc:
            await session.rollback()
            if job_id is not None:
                try:
                    await repos.jobs.update_status(
                        job_id,
                        "failed",
                        last_error=f"{type(exc).__name__}: {exc}"[:1000],
                        payload={"source_id": result.source_id, "stage": _last_stage(stages)},
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001 —— 记失败失败不能掩盖原始异常
                    await session.rollback()
            raise


def _last_stage(stages: list[tuple[str, float]]) -> str:
    return stages[-1][0] if stages else "init"


def _default_factory() -> async_sessionmaker[AsyncSession]:
    from app.core.db import session_factory

    return session_factory


def run_import(file_path: str | Path, **kwargs: Any) -> ImportResult:
    """同步门面(CLI 用)。**不能在已有事件循环里调用**;arq 请用 ``run_import_async``。"""
    return asyncio.run(run_import_async(file_path, **kwargs))
