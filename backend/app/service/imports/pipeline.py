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
from app.service.llm import LLMClient
from app.service.media import store_media
from app.service.parse import ParseResult, ParseStatus, parse_file
from app.service.parse.blocks import empty_doc
from app.service.rewrite.organizer import OrganizeResult, organize_note
from app.service.rewrite.outline import NUMBER_PREFIX

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
    """doc 首个 heading 文本(去编号);无 heading 退化文件名 stem。

    去编号:organizer 重写后 heading 被 renumber 加了「一、」「2.1」前缀,
    当笔记标题不雅——剥掉(复用 outline.NUMBER_PREFIX,与 renumber 同款)。
    原始 H1 无编号,不受影响。
    """
    for block in doc.get("content") or []:
        if block.get("type") == "heading":
            text = "".join(n.get("text", "") for n in block.get("content") or []).strip()
            if text:
                return NUMBER_PREFIX.sub("", text).strip()
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


# 视频关键帧 sidecar：worker 把 VideoContent.frame_images 写成「<md 文件名>.frames.json」，
# pipeline 读到它就知道 baleen-image:N → media_key 的映射（帧字节已在 storage/media/）。
VIDEO_FRAMES_SUFFIX = ".frames.json"


def _append_video_log(meta: dict, msg: str, pct: int | None = None) -> dict:
    """往 meta 追加一条视频进度日志(纯函数,不碰 DB)。

    条目结构 ``{ts, stage, pct}`` —— 时间戳(epoch 秒)+阶段文本+百分比。
    去重守卫:末条 stage 与 msg 相同则只刷新其 pct(防御未来同阶段连续调用刷屏);
    截断保留最近 50 条。worker 与 pipeline 各自调用、各自 session 落库,
    前端 2s 轮询读 ``parse_meta.video_log`` 渲染白盒日志区。
    """
    out = dict(meta)
    entries = list(out.get("video_log") or [])
    if entries and entries[-1].get("stage") == msg:
        # 同阶段连续调用:只刷新百分比,不新增条目(避免未来 whisper 之类刷屏)
        if pct is not None:
            entries[-1] = {**entries[-1], "pct": pct}
    else:
        entries.append({"ts": int(time.time()), "stage": msg, "pct": pct})
    out["video_log"] = entries[-50:]
    return out


def _load_video_frames(path: Path) -> list[dict] | None:
    """读视频关键帧 sidecar(与暂存 md 同目录);没有返回 None(普通文件导入路径)。"""
    import json

    sidecar = path.with_name(path.name + VIDEO_FRAMES_SUFFIX)
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None


def _video_image_resolver(video_images: list[dict]):
    """``baleen-image:N`` → image 块 attrs（media key 来自 sidecar 的帧清单）。"""
    from app.service.parse.md_parser import IMAGE_SCHEME

    by_index = {int(img.get("index", i)): img for i, img in enumerate(video_images)}

    def resolve(src: str, alt: str) -> dict | None:
        if not src.startswith(IMAGE_SCHEME):
            # 非视频帧的普通图片（md 里外链等）：保留 src 引用，交给 media_sink 逻辑不合适，
            # 这里只在视频 sidecar 场景兜底成「落不了地」——video md 里不该有别的图
            return None
        try:
            index = int(src[len(IMAGE_SCHEME):])
        except ValueError:
            return None
        img = by_index.get(index)
        if img is None or not img.get("media_key"):
            return None
        restored = {"media": img["media_key"], "alt": alt or str(img.get("alt") or "")}
        if img.get("timestamp") is not None:
            restored["timestamp"] = img["timestamp"]
        return restored

    return resolve


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


# video_ 前缀之外、必须跨 G0 落盘保留的 parse_meta 键(2026-11-26 C4:白名单改前缀约定)。
# 新增前缀外的保留键才需要动这里;video_* 键自动保留,不用手工维护。
_VIDEO_KEEP_EXTRA = frozenset(
    {
        "content_sha256",  # 上传去重键(名字不带 video_ 但视频导入同样依赖)
        "llm_transcript_fix",  # 转写 LLM 自校正留痕(2026-11-22 治本批)
    }
)


def video_keep_meta(prev_meta: dict) -> dict:
    """worker 阶段写进 parse_meta 的视频键,G0 落盘时保留的部分。

    约定(旧 7 键硬编码白名单退役):worker/转写/清理阶段往 parse_meta 写**新键**
    一律用 ``video_`` 前缀命名 → 本函数自动放行;忘加白被 G0 静默丢的坑(治本批
    踩过)从机制上根除。前缀外的保留键列 ``_VIDEO_KEEP_EXTRA``。
    """
    return {
        k: v
        for k, v in prev_meta.items()
        if k.startswith("video_") or k in _VIDEO_KEEP_EXTRA
    }


async def run_import_async(
    file_path: str | Path,
    *,
    owner_id: int = 1,
    no_llm: bool = False,
    dry_run: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    charter: str | None = None,
    client: LLMClient | None = None,
    prompt_budget: int | None = None,
    output_budget: int | None = None,
    allow_diagrams: bool = False,
    embed_func: EmbedFunc | None = None,
    token_counter: TokenCounter | None = None,
    worker: str = WORKER,
    source_id: int | None = None,
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
    job_id: int | None = None
    note_id: int | None = None

    async with factory() as session:
        repos = Repos(session, owner_id)
        try:
            # ---- 去重 + job + source ----
            t = time.perf_counter()
            if source_id is not None:
                # 视频异步路径：API 已预创建 source + job（占位 sha），直接用。
                # sha256 保持占位值（"video:{bvid}:whisper"），dedupe 基于 BVID；
                # 真实 sha 存入 parse_meta.content_sha256 供溯源。
                source = await repos.sources.get(source_id)
                if source is None:
                    raise ValueError(f"预创建 source {source_id} 不存在")
                job = await repos.jobs.get_by_dedupe_key(DEDUPE_PREFIX + source.sha256)
                if job is None:
                    raise ValueError(f"source {source_id} 对应的 job 不存在")
                job_id = job.id
                result.source_id = source_id
                storage_path = _store_blob(data, sha)
                await repos.sources.update_content(
                    source_id,
                    storage_path=str(storage_path),
                    size_bytes=len(data),
                    filename=path.name,
                    ext=path.suffix.lower(),
                    mime=mimetypes.guess_type(path.name)[0],
                )
                meta = dict(source.parse_meta or {})
                meta["content_sha256"] = sha
                meta.pop("video_stage", None)  # 清除进度标记，pipeline 接管
                await repos.sources.update_parse_meta(source_id, meta)
            else:
                dedupe_key = DEDUPE_PREFIX + sha
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
            # 视频路径:worker 在暂存 md 旁写了帧清单 sidecar(baleen-image:N → media_key)。
            # G0 解析 md 时用它把嵌图标记还原成 image 块(帧字节早已 store_media 存库)。
            video_images = _load_video_frames(path)
            if source.doc is not None and g0_run is not None:
                pr = _from_source(source)
            else:
                # blob 无扩展名(内容寻址),G0 按扩展名路由 → 解析原始路径(字节完全相同)。
                # client 随路下发:图片文件会走视觉转写(ADR 0006);no_llm 时不烧视觉。
                # md 里的本地图片经 media_sink 存字节(内容寻址),doc 只留 key(v16)
                if video_images:
                    # 视频 md 是流水线自己产的:图片标记按 sidecar 还原(不走 media_sink)
                    from app.service.parse.md_parser import parse_md_text

                    try:
                        raw_text = path.read_text(encoding="utf-8")
                        blocks, chars = parse_md_text(
                            raw_text, image_resolver=_video_image_resolver(video_images)
                        )
                        pr = ParseResult(
                            parse_status=ParseStatus.OK,
                            blocks={"type": "doc", "content": blocks},
                            meta={"engine": "video-markdown", "chars": chars},
                        )
                    except Exception:  # noqa: BLE001 —— 视频 md 解析异常退普通解析
                        pr = parse_file(path, client=None if no_llm else client, media_sink=store_media)
                else:
                    pr = parse_file(path, client=None if no_llm else client, media_sink=store_media)
                # 视频导入的进度字段在 worker 阶段写进 parse_meta，
                # G0 落盘时保留它们（否则 video_progress 等会被整体覆写丢失）
                prev_meta = dict(source.parse_meta or {})
                video_keep = video_keep_meta(prev_meta)
                g0_meta = {
                    **video_keep,
                    **(pr.meta or {}),
                    "warnings": pr.warnings,
                    "message": pr.message,
                    **({"video_frame_count": len(video_images)} if video_images else {}),
                }
                if video_keep.get("video_mode"):
                    # 视频导入:G0 解析完成,追加一条白盒日志(进度沿用 worker 末态)
                    g0_meta = _append_video_log(
                        g0_meta, "解析完成", pct=video_keep.get("video_progress")
                    )
                await repos.sources.update_parse(
                    source_id,
                    parse_status=pr.parse_status.value,
                    parse_meta=g0_meta,
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

            # ---- AI 笔记整理（替代 G2 归属闸 + 标题整理）----
            # no_llm=True（内部路径：chat save-note，内容已被 LLM 整理过）→ 跳过
            # no_llm=False → 读 note-format.md 契约 → LLM 重写 markdown → 更新 blocks
            original_title = _title(pr.blocks, path.name)
            note_title = original_title  # organizer 重写 blocks 后会用新首 H1 重算
            org: OrganizeResult | None = None
            if no_llm:
                result.g2_verdict = None
            else:
                t = time.perf_counter()
                g2_run = await repos.review_runs.get(source_id, "G2")
                if g2_run is not None:
                    # 断点续跑：organizer 已跑过，直接读结果
                    result.g2_verdict, result.quality, result.action = (
                        g2_run.verdict,
                        g2_run.quality,
                        g2_run.action,
                    )
                else:
                    # 视频导入:organizer 跑几十秒~几分钟,先落「AI 整理笔记中」并提交,
                    # 让前端轮询立刻看到(基于 DB 最新 parse_meta,绕过 ORM 缓存)
                    vmeta = await repos.sources.get_parse_meta(source_id)
                    if vmeta and vmeta.get("video_mode"):
                        await repos.sources.update_parse_meta(
                            source_id, _append_video_log(vmeta, "AI 整理笔记中")
                        )
                        await session.commit()
                    try:
                        org = await asyncio.to_thread(
                            organize_note,
                            pr,
                            filename=path.name,
                            ext=path.suffix.lower(),
                            size_bytes=len(data),
                            client=client,
                            prompt_budget=prompt_budget,
                            output_budget=output_budget,
                            video_images=video_images,  # 视频:多模态喂帧图 + 视频 prompt 约束
                            allow_diagram=allow_diagrams,  # 结构图(仍需依据闸命中才画)
                        )
                    except Exception as exc:  # noqa: BLE001 — LLM 网络异常，降级原文入库
                        log.warning("AI 笔记整理异常(降级原文入库): %s", exc)
                        org = OrganizeResult(
                            organizable=True,
                            blocks=pr.blocks,
                            quality="normal",
                            reasons=[f"AI 整理异常({type(exc).__name__})，已按原文入库"],
                        )
                    await repos.review_runs.add(
                        source_id=source_id,
                        gate="G2",
                        verdict="accept" if org.organizable else "reject",
                        quality=org.quality,
                        note_type=org.note_type,
                        action="index" if org.organizable else "drop",
                        reasons=org.reasons,
                        model=org.model,
                        cost_tokens=org.cost_tokens,
                        latency_ms=int(round(org.latency_ms)),
                    )
                    result.g2_verdict = "accept" if org.organizable else "reject"
                    result.quality = org.quality
                    result.action = "index" if org.organizable else "drop"
                    # 视频导入:organizer 完成,追加「AI 整理完成」(随下方 commit 落库)
                    vmeta = await repos.sources.get_parse_meta(source_id)
                    if vmeta and vmeta.get("video_mode"):
                        await repos.sources.update_parse_meta(
                            source_id, _append_video_log(vmeta, "AI 整理完成")
                        )
                await session.commit()
                mark("G2", t)
                if result.g2_verdict == "reject":
                    return await _finish(
                        repos, job_id, result, None, session,
                        capture=_capture_info(result, path, sha, owner_id, worker, None),
                    )
                # organizer 返回了重写后的 blocks → 替换（流向 note.doc 与 chunk）
                if org is not None and org.blocks is not None:
                    pr.blocks = org.blocks
                    note_title = _title(pr.blocks, path.name)  # 重写后的首 H1 当标题(去编号)
                    result.headings_rewritten = True
                    # 基于 DB 最新 parse_meta(绕过 ORM 缓存),避免覆盖刚写的 video_log
                    meta = await repos.sources.get_parse_meta(source_id) or {}
                    meta["headings_rewritten"] = True
                    source.parse_meta = meta
                    log.info("AI 笔记整理完成: %s → %s", original_title, note_title)

            # ---- note ----
            t = time.perf_counter()
            note = await repos.notes.get_by_source(source_id)
            if note is None:
                note = await repos.notes.create(
                    source_id=source_id,
                    title=note_title,
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
