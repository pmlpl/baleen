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

from arq import create_pool, func
from arq.connections import RedisSettings

from app.core.config import ROOT_DIR, get_settings
from app.core.db import session_factory
from app.repo import Repos
from app.repo.qa import QaRepo
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
    from app.service.llm.resolve import (
        resolve_allow_diagrams,
        resolve_charter,
        resolve_llm_budgets,
        resolve_llm_config,
    )

    # 读该用户的 charter 与 LLM 配置(api_key 不继承 .env;没配就传 None,流水线走降级口径)
    async with session_factory() as session:
        charter = await resolve_charter(session, owner_id)
        base_url, api_key, model = await resolve_llm_config(session, owner_id)
        prompt_budget, output_budget = await resolve_llm_budgets(session, owner_id)
        allow_diagrams = await resolve_allow_diagrams(session, owner_id)
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
        prompt_budget=prompt_budget,
        output_budget=output_budget,
        allow_diagrams=allow_diagrams,
    )
    if no_llm and not result.deduped and result.source_id is not None:
        await _mark_g2_skipped(result.source_id, owner_id)
    _remove_staging(file_path)
    return asdict(result)


async def enqueue_import(
    file_path: str, *, dedupe_key: str, no_llm: bool = False, owner_id: int
) -> str | None:
    """入队导入任务。以 dedupe_key 作 arq job id:同 key 的在途任务会被 arq 幂等挡掉
    (返回 None),重复上传/重复入队都安全。每次调用各开各的连接池,用完即关。

    重试场景:先删掉上一次失败的 arq job/result 键,否则失败结果在 Redis 保留期间
    同 ID 入队会被静默丢弃(用户重新上传失败过的文件永远不生效)。

    ``owner_id`` 必传:决定导入产物归属哪个账号(多用户后不能默认成 1)。
    dedupe_key 生产方应带上 owner 前缀,避免不同用户同内容互相去重。
    """
    from arq.constants import job_key_prefix, result_key_prefix

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.delete(f"{job_key_prefix}{dedupe_key}", f"{result_key_prefix}{dedupe_key}")
        job = await pool.enqueue_job(
            "import_file", file_path, no_llm=no_llm, owner_id=owner_id, _job_id=dedupe_key
        )
        return job.job_id if job is not None else None
    finally:
        await pool.aclose()


async def _update_video_stage(
    source_id: int,
    stage: str,
    owner_id: int,
    *,
    progress: int | None = None,
    est_seconds: int | None = None,
) -> None:
    """更新 source.parse_meta 的视频进度字段，供前端轮询显示进度条。

    独立 DB session，不影响 pipeline 的 session。每次埋点同时往 video_log 追加
    一条白盒日志(stage+progress)，前端渲染滚动日志区。
    """
    from app.service.imports.pipeline import _append_video_log

    async with session_factory() as session:
        repos = Repos(session, owner_id=owner_id)
        source = await repos.sources.get(source_id)
        if source is None:
            return
        meta = dict(source.parse_meta or {})
        meta["video_stage"] = stage
        if progress is not None:
            meta["video_progress"] = max(0, min(100, int(progress)))
        if est_seconds is not None:
            meta["video_est_seconds"] = int(est_seconds)
        meta = _append_video_log(meta, stage, progress)
        await repos.sources.update_parse_meta(source_id, meta)
        await session.commit()


def _make_progress_cb(source_id: int, owner_id: int, loop: asyncio.AbstractEventLoop):
    """线程安全进度回调：extract_video_content 在 to_thread 线程里同步调 cb，
    通过 run_coroutine_threadsafe 调度回 worker 事件循环写 DB。"""
    def cb(stage: str, pct: int, est_seconds: int | None = None) -> None:
        asyncio.run_coroutine_threadsafe(
            _update_video_stage(source_id, stage, owner_id, progress=pct, est_seconds=est_seconds),
            loop,
        )
    return cb


def _make_bilibili_login_cb(source_id: int, owner_id: int, loop: asyncio.AbstractEventLoop):
    """B站兜底缺登录态（未配置/SESSDATA 过期）时的钩子（video_extractor 调用，2026-11-19）。

    跑在 to_thread 的工作线程里：写进度「请扫码」→ 弹真浏览器扫码（阻塞 ≤180s，
    占住 max_jobs=1 队列——数月一次的事件，可接受）→ 扫码结果落 app_settings（热生效）
    → 返回新 SESSDATA 给原地重试。失败抛翻译好的中文 RuntimeError，走 _fail_video_job
    常规失败路径（前端 last_error 直接可读）。DB/进度操作用 run_coroutine_threadsafe
    切回 worker 事件循环（同 _make_progress_cb 的做法）。
    """
    async def _stage(text: str) -> None:
        await _update_video_stage(source_id, text, owner_id)

    def cb() -> str:
        from app.service.bilibili_login import (
            BiliLoginCancelled,
            BiliLoginError,
            BiliLoginTimeout,
            qr_login_bilibili,
            save_bilibili_login,
        )

        asyncio.run_coroutine_threadsafe(
            _stage("⏰ 请扫码登录 B 站（浏览器窗口已弹出，180 秒内扫码）"), loop
        ).result()
        try:
            result = qr_login_bilibili(timeout_s=180.0)
        except BiliLoginCancelled as exc:
            raise RuntimeError(
                "B 站扫码登录已取消（浏览器窗口被关闭）。重新导入会再次弹出扫码窗口"
            ) from exc
        except BiliLoginTimeout as exc:
            raise RuntimeError(
                "等待扫码超时（180 秒）。请在电脑前重新导入（会再次弹出扫码窗口），"
                "或提前到「视频笔记」页点「B站登录」扫码"
            ) from exc
        except BiliLoginError as exc:
            raise RuntimeError(f"B 站扫码登录失败：{exc}") from exc

        async def _save() -> None:
            async with session_factory() as session:
                await save_bilibili_login(session, owner_id, result)
                await session.commit()

        asyncio.run_coroutine_threadsafe(_save(), loop).result()
        asyncio.run_coroutine_threadsafe(_stage("扫码成功，继续提取…"), loop).result()
        return result.sessdata

    return cb


async def _fail_video_job(source_id: int, owner_id: int, exc: Exception) -> None:
    """提取阶段失败：标记 job failed + source.parse_meta 显示错误。"""
    from app.service.imports.pipeline import DEDUPE_PREFIX, _append_video_log

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
            err_stage = f"❌ {type(exc).__name__}: {str(exc)[:80]}"
            meta["video_stage"] = err_stage
            meta = _append_video_log(meta, err_stage)
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

    ``sessdata`` 参数已废弃（2026-11-19）：仅为兼容 Redis 里带旧 kwarg 的在途任务保留，
    实际值在任务入口从 app_settings 现读（resolve_bilibili_sessdata）——扫码落库后
    下一条任务（含扫码后的原地重试）立即用上新值，无需重启。
    """
    import uuid

    from app.service.imports import run_import_async
    from app.service.imports.pipeline import DEDUPE_PREFIX, VIDEO_FRAMES_SUFFIX
    from app.service.llm.resolve import (
        resolve_allow_diagrams,
        resolve_bilibili_sessdata,
        resolve_charter,
        resolve_llm_budgets,
        resolve_llm_config,
    )

    # ---- Phase 0: 幂等守卫(2026-11-19) ----
    # arq 对非正常关机残留 in-progress 的 job 重启即重投递(实测:11-18 晚完成的
    # 6014/6015 在 11-19 早被整套重跑约 29 分钟,白烧下载+whisper+视觉)。导入已完成
    # (source ok 且笔记还在)直接标 done 返回。笔记被删时孤儿 source 会连带清理
    # (DELETE notes 级联),所以「source ok 但无笔记」不拦,照常重跑。
    async with session_factory() as session:
        repos = Repos(session, owner_id=owner_id)
        source = await repos.sources.get(source_id)
        if source is not None and source.parse_status == "ok":
            note = await repos.notes.get_by_source(source_id)
            if note is not None:
                job = await repos.jobs.get_by_dedupe_key(DEDUPE_PREFIX + source.sha256)
                if job is not None:
                    await repos.jobs.update_status(
                        job.id, "done",
                        payload={"deduped": True, "source_id": source_id, "note_id": note.id},
                    )
                await session.commit()
                log.info("视频 job 重投递被幂等守卫拦截: source=%s note=%s", source_id, note.id)
                return {"deduped": True, "source_id": source_id, "note_id": note.id}

    # 解析 LLM 配置(同 import_file) + B站 SESSDATA(app_settings 现读,扫码落库后热生效)
    async with session_factory() as session:
        charter = await resolve_charter(session, owner_id)
        base_url, api_key, model = await resolve_llm_config(session, owner_id)
        prompt_budget, output_budget = await resolve_llm_budgets(session, owner_id)
        allow_diagrams = await resolve_allow_diagrams(session, owner_id)
        sessdata = await resolve_bilibili_sessdata(session, owner_id)
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
    # 抖音走 video_extractor 内部的 Playwright 免 cookie 路径（2026-11-14）。
    # progress_cb 把阶段/百分比回写 parse_meta，前端画进度条（whisper 阶段前端按 est 插值）。
    loop = asyncio.get_running_loop()
    progress_cb = _make_progress_cb(source_id, owner_id, loop)
    try:
        if mode == "whisper":
            from app.service.video_extractor import extract_video_content

            await _update_video_stage(source_id, "获取视频中…", owner_id, progress=8)
            vc = await asyncio.to_thread(
                extract_video_content, url, sessdata=sessdata, client=client, progress_cb=progress_cb,
                bilibili_login_cb=_make_bilibili_login_cb(source_id, owner_id, loop),
            )
            markdown_content = vc.markdown
            video_title = vc.title
            if not vc.frame_images:
                # 6014 教训:帧全军覆没不能静默——候选>0 全被筛除/提取失败/场景数为 0,
                # 都写进白盒日志(video_log 持久化在 parse_meta,导入完成后仍可见)
                await _update_video_stage(
                    source_id,
                    f"⚠️ 画面帧未进入笔记(候选 {vc.frame_candidates} 张,全部被筛除或提取失败)",
                    owner_id,
                )
        else:
            from app.service.bilibili import fetch_bilibili, sanitize_filename

            await _update_video_stage(source_id, "拉取字幕中…", owner_id)
            result = await asyncio.to_thread(fetch_bilibili, url, sessdata=sessdata)
            markdown_content = result.markdown
            video_title = result.info.title
    except Exception as exc:
        # 把 yt-dlp 的原始报错翻译成人话（前端直接显示 last_error）
        msg = str(exc)
        if "Fresh cookies" in msg:
            friendly = RuntimeError(
                "抖音拒绝访问（Cookies 无效、过期或不完整）。"
                "请到「设置」页重新配置「抖音 Cookies」：打开 douyin.com → F12 → Network → 刷新页面 → "
                "点一个 douyin.com 请求 → Request Headers → 复制整个 Cookie 的值（必须包含 msToken，"
                "只复制 ttwid 单个值不够）→ 粘贴保存，再重新导入。"
            )
            await _fail_video_job(source_id, owner_id, friendly)
            raise friendly from exc
        if "Unsupported URL" in msg or "Unable to extract" in msg:
            friendly = RuntimeError(
                "无法识别这个视频链接。请确认粘贴的是 B 站或抖音的视频链接（整段分享文本也可以）。"
            )
            await _fail_video_job(source_id, owner_id, friendly)
            raise friendly from exc
        if "HTTP Error 404" in msg or "Video unavailable" in msg:
            friendly = RuntimeError("视频不存在或已被删除，请检查链接。")
            await _fail_video_job(source_id, owner_id, friendly)
            raise friendly from exc
        await _fail_video_job(source_id, owner_id, exc)
        raise

    # ---- Phase 2: 写 markdown 到暂存区 ----
    from app.service.bilibili import sanitize_filename

    filename = sanitize_filename(video_title) + ".md"
    staging_dir = STAGING_ROOT / uuid.uuid4().hex
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / filename
    staging.write_text(markdown_content, encoding="utf-8")

    # 视频关键帧清单 sidecar：pipeline G0/ organizer 靠它把 baleen-image:N 还原成
    # image 块（含 media_key）并多模态喂图。写在 md 同目录 → worker 清理暂存时一并删。
    frame_images = getattr(vc, "frame_images", None) if mode == "whisper" else None
    if frame_images:
        import json

        sidecar = staging.with_name(staging.name + VIDEO_FRAMES_SUFFIX)
        sidecar.write_text(json.dumps(frame_images, ensure_ascii=False), encoding="utf-8")

    # ---- Phase 3: 跑 pipeline（用预创建 source + job）----
    result = await run_import_async(
        str(staging),
        no_llm=False,
        worker=WORKER_NAME,
        charter=charter,
        owner_id=owner_id,
        client=client,
        prompt_budget=prompt_budget,
        output_budget=output_budget,
        allow_diagrams=allow_diagrams,
        source_id=source_id,
    )

    # 清理暂存（成功才删，失败保留供重试）
    if not result.deduped:
        _remove_staging(str(staging))

    return asdict(result)


async def enqueue_video_extract(
    url: str, *, mode: str, source_id: int, owner_id: int,
    sessdata: str = "",
) -> str | None:
    """入队视频提取任务。

    超时 2 小时在 WorkerSettings.func(timeout=7200) 定义（enqueue_job 没有 _timeout 参数）。
    ``_job_id`` 用 source_id 保证幂等（同一 source 不会重复入队）。
    重试场景：先删掉上一次失败的 arq job/result 键，否则 enqueue_job 会因同 ID
    已存在而被静默丢弃（用户配好 cookies 重试就永远不生效）。
    sessdata 用于 B站；抖音 cookies 由 worker 从 DB 设置页读取（不需要 API 传）。
    """
    from arq.constants import job_key_prefix, result_key_prefix

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job_id = f"video:{source_id}"
        await pool.delete(f"{job_key_prefix}{job_id}", f"{result_key_prefix}{job_id}")
        job = await pool.enqueue_job(
            "extract_and_import_video", url,
            mode=mode, source_id=source_id, owner_id=owner_id,
            sessdata=sessdata,
            _job_id=job_id,
        )
        return job.job_id if job is not None else None
    finally:
        await pool.aclose()


async def digest_conversation(
    ctx: dict,
    session_id: int,
    *,
    owner_id: int,
    start_msg_id: int,
    end_msg_id: int,
) -> dict:
    """arq 任务:把 [start_msg_id, end_msg_id] 区间的对话冻结成一段不可变 digest。

    fire-and-forget:chat 端点 commit 后入队,本任务异步生成摘要并落 qa_digests。
    失败向上抛(arq 自动重试);幂等键 ``digest:{session_id}:{end_msg_id}`` 保证同区间
    不重复冻结。没配 api_key 的用户跳过(近窗继续累积,安全网兜底,不崩)。
    """
    from app.service.llm.resolve import resolve_llm_config
    from app.service.qa.digest import dialogue_from_messages, run_digest

    async with session_factory() as session:
        repo = QaRepo(session, owner_id)
        msgs = await repo.list_messages_in_range(session_id, start_msg_id, end_msg_id)
        if not msgs:
            return {"session_id": session_id, "digest_id": None, "skipped": "no_messages"}
        dialogue = dialogue_from_messages(msgs)
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
        if client is None:
            return {"session_id": session_id, "digest_id": None, "skipped": "no_api_key"}
        summary = await run_digest(dialogue, client=client)
        if not summary:
            return {"session_id": session_id, "digest_id": None, "skipped": "empty_summary"}
        digest = await repo.add_digest(
            session_id,
            start_msg_id=start_msg_id,
            end_msg_id=end_msg_id,
            summary=summary,
        )
        await session.commit()
        return {
            "session_id": session_id,
            "digest_id": digest.id,
            "summary_len": len(summary),
        }


async def enqueue_digest(
    session_id: int, *, owner_id: int, start_msg_id: int, end_msg_id: int
) -> str | None:
    """入队冻结摘要任务。

    ``_job_id = digest:{session_id}:{end_msg_id}`` 保证同一区间幂等(不会重复冻结)。
    重试场景先删 arq job/result 键(失败结果在 Redis 保留期间同 ID 入队会被静默丢弃,
    用户重新提问触发冻结就永远不生效)。
    """
    from arq.constants import job_key_prefix, result_key_prefix

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        job_id = f"digest:{session_id}:{end_msg_id}"
        await pool.delete(f"{job_key_prefix}{job_id}", f"{result_key_prefix}{job_id}")
        job = await pool.enqueue_job(
            "digest_conversation",
            session_id,
            owner_id=owner_id,
            start_msg_id=start_msg_id,
            end_msg_id=end_msg_id,
            _job_id=job_id,
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
    视频提取任务(extract_and_import_video)用 func(timeout=7200) 单独给 2 小时
    (arq 的 enqueue_job 没有 _timeout 参数——传了会被当 kwargs 转发给任务函数直接 TypeError)。
    """

    functions = [
        import_file,
        func(extract_and_import_video, timeout=7200),
        digest_conversation,
    ]
    on_startup = on_startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 1
    job_timeout = 900
