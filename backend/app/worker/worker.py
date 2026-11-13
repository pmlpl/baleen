"""arq worker:上传文件 → 导入流水线(线 I-批2)。

- 任务 ``import_file`` 直接 ``await run_import_async``(批 1 断点:流水线有异步核心,
  不要再套 asyncio.run / asyncio.to_thread);
- 暂存区生命周期归 worker 管:任务成功后删暂存文件,失败保留供 arq 重试;
  worker 启动时清理超过 24h 的孤儿文件(协调方拍板方案 a);
- Redis 地址复用 config.redis_url;启动命令:
  ``cd backend && uv run arq app.worker.worker.WorkerSettings``。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import ROOT_DIR, get_settings
from app.core.db import session_factory
from app.repo import Repos
from app.service.llm import LLMClient
from app.service.parse.blocks import empty_doc

log = logging.getLogger(__name__)

# 暂存目录:上传 API 先把文件落在这里(保留原文件名/扩展名供 G0 路由),
# blob 由流水线照常按 storage/{sha[:2]}/{sha} 存(AGENTS §4)
STAGING_ROOT = ROOT_DIR / "storage" / "uploads"
STALE_UPLOAD_TTL_HOURS = 24
WORKER_NAME = "arq-worker"


def _remove_staging(file_path: str | Path) -> bool:
    """删除暂存内容。只删 STAGING_ROOT 之下的路径(任务参数不可信,防误删)。

    v16 起每次上传是一个目录(主文件 + 附件);兼容旧的平铺文件路径。
    """
    path = Path(file_path).resolve()
    root = STAGING_ROOT.resolve()
    if root not in path.parents:
        return False
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return True
    path.unlink(missing_ok=True)
    # 顺手清掉已空的上传目录(主文件删掉后附件就是孤儿)
    parent = path.parent
    if parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    return True


def clean_stale_uploads(max_age_hours: int = STALE_UPLOAD_TTL_HOURS) -> list[str]:
    """清理暂存区里超过 max_age_hours 的孤儿条目(文件或上传目录),返回被删名称。

    孤儿来源:上传后任务一直没成功(失败保留供重试)、重复上传在途任务被 arq 幂等挡掉等。
    成功导入的暂存内容在任务结束时当场已删,不会出现在这里。
    """
    removed: list[str] = []
    if not STAGING_ROOT.is_dir():
        return removed
    cutoff = time.time() - max_age_hours * 3600
    for entry in STAGING_ROOT.iterdir():
        try:
            stale = entry.stat().st_mtime < cutoff
        except OSError:
            continue
        if not stale:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed.append(entry.name)
    return removed


async def _mark_g2_skipped(source_id: int, owner_id: int) -> None:
    """no_llm 导入成功后打标记:parse_meta.g2_skipped=true;G2 的 review_runs 行保持不存在。

    写在 worker 而不是流水线里:本批文件边界不含 app/service/imports/(协调方确认)。
    之后按 JSONB 条件即可查出所有跳过 G2 的 source 批量补跑。
    dedupe 命中(之前带 G2 导入过)时不打,避免给真跑过 G2 的 source 冤记标记。
    """
    async with session_factory() as session:
        repos = Repos(session, owner_id=owner_id)
        source = await repos.sources.get(source_id)
        if source is None:
            return
        meta = dict(source.parse_meta or {})
        meta["g2_skipped"] = True
        await repos.sources.update_parse(
            source_id,
            parse_status=source.parse_status,
            parse_meta=meta,
            doc=source.doc or empty_doc(),
        )
        await session.commit()


async def import_file(
    ctx: dict, file_path: str, *, no_llm: bool = False, owner_id: int = 1
) -> dict:
    """arq 任务:跑导入流水线。失败向上抛(arq 自动重试,流水线可续跑);成功删暂存文件。

    ``owner_id`` 由入队方(upload / save-note)传入,决定数据落在哪个账号下;
    默认 1 只兼容历史任务,新任务必须显式传。
    """
    # 延迟导入:上传 API 进程 import 本模块(拿 STAGING_ROOT/enqueue_import)时,
    # 不把 torch/解析栈拉进 api 进程
    from app.service.imports import run_import_async
    from app.service.llm.resolve import resolve_charter, resolve_llm_config

    # 读该用户的 charter 与 LLM 配置(api_key 不继承 .env;没配就传 None,流水线走降级口径)
    async with session_factory() as session:
        charter = await resolve_charter(session, owner_id)
        base_url, api_key, model = await resolve_llm_config(session, owner_id)
    # 导入是后台任务,允许长耗时:标题整理要整篇过一遍,60s 默认超时不够
    client = (
        LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=get_settings().llm_long_task_timeout,
        )
        if api_key
        else None
    )

    result = await run_import_async(
        file_path,
        no_llm=no_llm,
        worker=WORKER_NAME,
        charter=charter,
        owner_id=owner_id,
        client=client,
    )
    if no_llm and not result.deduped and result.source_id is not None:
        await _mark_g2_skipped(result.source_id, owner_id)
    _remove_staging(file_path)
    # 阶段3(ADR 0005):导入成功 → 摘要 + 自动建「新导入:xxx」会话。
    # best-effort:失败只记日志,不影响导入结果(ADR §2 验收标准)。
    if result.note_id is not None and not result.deduped and not no_llm:
        try:
            from app.service.qa.summarizer import summarize_import

            qa_session = await summarize_import(
                result.note_id, Path(file_path).name, owner_id=owner_id
            )
            if qa_session is not None:
                log.info(
                    "导入后摘要会话已建: qa_sessions #%s(笔记 #%s)", qa_session.id, result.note_id
                )
        except Exception as exc:  # noqa: BLE001 —— 摘要是锦上添花,静默跳过(ADR 0005 §2)
            log.warning("导入后摘要生成失败(不影响导入): %s", exc)
    return asdict(result)


async def enqueue_import(
    file_path: str, *, dedupe_key: str, no_llm: bool = False, owner_id: int
) -> str | None:
    """入队导入任务。以 dedupe_key 作 arq job id:同 key 的在途任务会被 arq 幂等挡掉
    (返回 None),重复上传/重复入队都安全。每次调用各开各的连接池,用完即关。

    ``owner_id`` 必传:决定导入产物归属哪个账号(多用户后不能默认成 1)。
    dedupe_key 生产方应带上 owner 前缀,避免不同用户同内容互相去重。
    """
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job = await pool.enqueue_job(
            "import_file", file_path, no_llm=no_llm, owner_id=owner_id, _job_id=dedupe_key
        )
        return job.job_id if job is not None else None
    finally:
        await pool.aclose()


async def _update_video_stage(source_id: int, stage: str, owner_id: int) -> None:
    """更新 source.parse_meta["video_stage"]，供前端轮询显示提取进度。

    独立 DB session，不影响 pipeline 的 session。
    """
    async with session_factory() as session:
        repos = Repos(session, owner_id=owner_id)
        source = await repos.sources.get(source_id)
        if source is None:
            return
        meta = dict(source.parse_meta or {})
        meta["video_stage"] = stage
        await repos.sources.update_parse_meta(source_id, meta)
        await session.commit()


async def _fail_video_job(source_id: int, owner_id: int, exc: Exception) -> None:
    """提取阶段失败：标记 job failed + source.parse_meta 显示错误。"""
    from app.service.imports.pipeline import DEDUPE_PREFIX

    async with session_factory() as session:
        repos = Repos(session, owner_id=owner_id)
        source = await repos.sources.get(source_id)
        if source is not None:
            job = await repos.jobs.get_by_dedupe_key(DEDUPE_PREFIX + source.sha256)
            if job is not None:
                await repos.jobs.update_status(
                    job.id, "failed",
                    last_error=f"{type(exc).__name__}: {exc}"[:1000],
                )
            meta = dict(source.parse_meta or {})
            meta["video_stage"] = f"❌ {type(exc).__name__}: {str(exc)[:80]}"
            await repos.sources.update_parse_meta(source_id, meta)
        await session.commit()


async def extract_and_import_video(
    ctx: dict,
    url: str,
    *,
    owner_id: int,
    source_id: int,
    mode: str = "whisper",
    sessdata: str = "",
    cookies_file: str = "",
) -> dict:
    """arq 任务：异步视频提取 → 写 markdown → 跑 pipeline。

    替代 upload.py 中的同步 ``extract_video_content`` 调用。
    API 立即返回 201，worker 在后台完成：
    1. 下载音频 → whisper 转写（或字幕模式拉字幕）
    2. 写 markdown 到暂存区
    3. 调 ``run_import_async(source_id=...)`` — pipeline 用预创建的 source+job

    进度通过 source.parse_meta["video_stage"] 传达给前端轮询。
    提取失败 → 标记 job failed + re-raise（arq 自动重试）。
    pipeline 失败 → pipeline 自己的异常处理器标记 job failed。
    """
    import uuid

    from app.service.imports import run_import_async
    from app.service.llm.resolve import resolve_charter, resolve_llm_config

    # 解析 LLM 配置（同 import_file）
    async with session_factory() as session:
        charter = await resolve_charter(session, owner_id)
        base_url, api_key, model = await resolve_llm_config(session, owner_id)
    client = (
        LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=get_settings().llm_long_task_timeout,
        )
        if api_key
        else None
    )

    # ---- Phase 1: 提取（下载音频 → whisper / 拉字幕）----
    try:
        if mode == "whisper":
            from app.service.video_extractor import extract_video_content

            await _update_video_stage(source_id, "下载音频中…", owner_id)
            vc = await asyncio.to_thread(
                extract_video_content, url, sessdata=sessdata, cookies_file=cookies_file, client=client,
            )
            markdown_content = vc.markdown
            video_title = vc.title
        else:
            from app.service.bilibili import fetch_bilibili, sanitize_filename

            await _update_video_stage(source_id, "拉取字幕中…", owner_id)
            result = await asyncio.to_thread(fetch_bilibili, url, sessdata=sessdata)
            markdown_content = result.markdown
            video_title = result.info.title
    except Exception as exc:
        await _fail_video_job(source_id, owner_id, exc)
        raise

    # ---- Phase 2: 写 markdown 到暂存区 ----
    from app.service.bilibili import sanitize_filename

    filename = sanitize_filename(video_title) + ".md"
    staging_dir = STAGING_ROOT / uuid.uuid4().hex
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / filename
    staging.write_text(markdown_content, encoding="utf-8")

    # ---- Phase 3: 跑 pipeline（用预创建 source + job）----
    result = await run_import_async(
        str(staging),
        no_llm=False,
        worker=WORKER_NAME,
        charter=charter,
        owner_id=owner_id,
        client=client,
        source_id=source_id,
    )

    # 清理暂存（成功才删，失败保留供重试）
    if not result.deduped:
        _remove_staging(str(staging))

    # best-effort 摘要会话（同 import_file）
    if result.note_id is not None and not result.deduped:
        try:
            from app.service.qa.summarizer import summarize_import

            qa_session = await summarize_import(
                result.note_id, Path(staging).name, owner_id=owner_id,
            )
            if qa_session is not None:
                log.info(
                    "视频导入后摘要会话已建: qa_sessions #%s(笔记 #%s)",
                    qa_session.id, result.note_id,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("视频导入后摘要生成失败(不影响导入): %s", exc)

    return asdict(result)


async def enqueue_video_extract(
    url: str, *, mode: str, source_id: int, owner_id: int,
    sessdata: str = "", cookies_file: str = "",
) -> str | None:
    """入队视频提取任务。

    ``_timeout=7200``（2 小时）：whisper base GPU ~1x 实时，60 分钟视频 ≈ 60 分钟转写。
    ``_job_id`` 用 source_id 保证幂等（同一 source 不会重复入队）。
    sessdata 用于 B站；cookies_file 用于抖音等需要完整 cookie 文件的平台。
    """
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job = await pool.enqueue_job(
            "extract_and_import_video", url,
            mode=mode, source_id=source_id, owner_id=owner_id,
            sessdata=sessdata, cookies_file=cookies_file,
            _job_id=f"video:{source_id}",
            _timeout=7200,
        )
        return job.job_id if job is not None else None
    finally:
        await pool.aclose()


async def on_startup(ctx: dict) -> None:
    await asyncio.to_thread(clean_stale_uploads)


class WorkerSettings:
    """``cd backend && uv run arq app.worker.worker.WorkerSettings``。

    max_jobs=1:bge 在 GPU 上,导入串行;job_timeout=900s 按最坏 300s×3 倍余量定(协调方拍板)。
    no_llm 模式若仍超时说明有死循环,不许靠调大 timeout 糊过去。
    视频提取任务(extract_and_import_video)通过 enqueue 时的 _timeout=7200 单独给 2 小时。
    """

    functions = [import_file, extract_and_import_video]
    on_startup = on_startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 1
    job_timeout = 900
