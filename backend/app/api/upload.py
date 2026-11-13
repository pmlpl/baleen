"""POST /api/sources(上传入库)+ GET /api/sources/{id}(状态查询)。线 I-批2。

- 扩展名白名单复用 G0 的判定(PARSERS ∪ IMAGE_EXTS ∪ UNSUPPORTED_MESSAGES,协调方拍板):
  .doc/.xlsx 等会被收下、由 G0 给出「请转成 .docx」这类具体提示并记 unsupported_format;
  415 只留给系统完全不认识的扩展名。格式语义只存在 G0 一处。
- 大小上限 50 MB(样本最大 12 MB,4 倍余量);请求体上限 55 MB:先按 Content-Length 头
  挡一轮(在解析 multipart 之前),再流式落盘边读边限,不把大文件整个读进内存。
- dedupe:同 sha256 且 job done → 不再入队,返回已有 source;job 在途(running)不重复入队;
  其余情况(queued/failed)重复入队由 arq 的 _job_id 幂等挡掉。
- no_llm=true:流水线跳过 G2(不写 G2 判定行),worker 成功后在 parse_meta 打 g2_skipped=true,
  便于日后批量补跑 G2。
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentOwnerDep, get_session
from app.repo import Repos
from app.service.parse.router import IMAGE_EXTS, PARSERS, UNSUPPORTED_MESSAGES
from app.worker import STAGING_ROOT, enqueue_import, enqueue_video_extract

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
BODY_LIMIT_BYTES = 55 * 1024 * 1024
READ_CHUNK = 1024 * 1024
ALLOWED_EXTS = set(PARSERS) | set(IMAGE_EXTS) | set(UNSUPPORTED_MESSAGES)
# md 里引用的本地图片随主文件一起上传(v16):一次最多带这么多附件
MAX_ATTACHMENTS = 40
# 附件允许的扩展名:只有图片(md 的插图;其它类型没有引用场景)
ATTACHMENT_EXTS = set(IMAGE_EXTS)

router = APIRouter(prefix="/api", tags=["sources"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class UploadResponse(BaseModel):
    source_id: int
    job_id: int
    job_status: str
    deduped: bool


class UrlImportRequest(BaseModel):
    """POST /api/sources/url 的请求体:B 站视频链接导入。"""

    url: str
    mode: str = "subtitle"  # "subtitle"（秒级，拉B站字幕） | "whisper"（分钟级，下载音频+whisper转写）


async def _write_attachments(attachments: list[UploadFile], target_dir: Path) -> None:
    """附件按原始相对路径写进本次上传的暂存目录(md 里的相对引用才能解析)。

    multipart 里的文件名可能带路径(webkitdirectory 上传),保留相对层级但挡掉
    ``..`` 与绝对路径——绝不允许写出暂存目录。
    """
    root = target_dir.resolve()
    for att in attachments:
        raw = (att.filename or "").replace("\\", "/").lstrip("/")
        parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        target = (target_dir / Path(*parts)).resolve()
        if root not in target.parents:  # 防穿越(理论上上面已挡,.resolve 后双保险)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            while chunk := await att.read(READ_CHUNK):
                out.write(chunk)
        await att.close()


class JobOut(BaseModel):
    id: int
    status: str
    attempts: int
    last_error: str | None


class ReviewRunOut(BaseModel):
    gate: str
    verdict: str
    quality: str | None
    note_type: str | None
    score: float | None
    action: str | None
    reasons: list[str]
    model: str | None
    cost_tokens: int
    latency_ms: int


class SourceStatus(BaseModel):
    id: int
    filename: str
    ext: str
    size_bytes: int
    mime: str | None
    parse_status: str
    message: str | None
    warnings: list[str]
    g2_skipped: bool
    # 导入时是否跑过标题结构二次加工
    headings_rewritten: bool
    # 视频异步提取进度（下载音频中/whisper转写中/❌错误），非视频导入为 None
    video_stage: str | None = None
    note_id: int | None
    created_at: datetime
    job: JobOut | None
    review_runs: list[ReviewRunOut]


async def guard_body_size(request: Request) -> None:
    """请求体上限先挡一轮:依赖只声明 Request,会在解析 multipart 之前执行。"""
    raw = request.headers.get("content-length", "")
    if raw.isdigit() and int(raw) > BODY_LIMIT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"请求体超过上限:{BODY_LIMIT_BYTES // (1024 * 1024)} MB",
        )


@router.post("/sources", response_model=UploadResponse)
async def upload_source(
    _: Annotated[None, Depends(guard_body_size)],
    response: Response,
    file: UploadFile,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
    attachments: Annotated[
        list[UploadFile] | None,
        File(description="md 里引用的本地图片(v16):按相对路径随主文件一起上传"),
    ] = None,
) -> UploadResponse:
    # 延迟导入:api 进程不因 import 本模块而加载 torch/解析栈,只在真正上传时才拉
    from app.service.imports.pipeline import DEDUPE_PREFIX, _store_blob
    from app.service.llm.resolve import resolve_llm_config

    raw_name = Path(file.filename or "").name
    if not raw_name:
        raise HTTPException(status_code=422, detail="缺少文件名")
    ext = Path(raw_name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=415, detail=f"暂不支持的格式:{ext or '(无扩展名)'}")
    attachments = attachments or []
    if len(attachments) > MAX_ATTACHMENTS:
        raise HTTPException(status_code=413, detail=f"附件太多:一次最多 {MAX_ATTACHMENTS} 个")
    for att in attachments:
        att_ext = Path(Path(att.filename or "").name).suffix.lower()
        if att_ext not in ATTACHMENT_EXTS:
            raise HTTPException(
                status_code=415, detail=f"附件只支持图片:{att.filename or '(无文件名)'}"
            )

    # 预检:没配 API Key → 不允许上传(所有内容必须经 AI 笔记整理，防止未整理内容污染知识库)
    _base, api_key, _model = await resolve_llm_config(session, owner_id)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="这个账号还没配置 API Key,请先到设置页配置后再上传",
        )

    # 流式落暂存区:**每次上传建一个目录**,主文件按原文件名放进去、附件也放进去 ——
    # md 解析器按「相对 md 文件所在目录」解析图片路径,目录结构天然对上,
    # 且同一批上传的附件不会和别的上传串味(v16)。
    unique = uuid.uuid4().hex
    staging_dir = STAGING_ROOT / unique
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / raw_name
    digest = hashlib.sha256()
    size = 0
    try:
        with staging.open("wb") as out:
            while chunk := await file.read(READ_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过大小上限:{MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                    )
                digest.update(chunk)
                out.write(chunk)
        await _write_attachments(attachments, staging_dir)
    except HTTPException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    sha = digest.hexdigest()
    blob = _store_blob(staging.read_bytes(), sha)  # 内容寻址只有这一个实现(pipeline 私有函数)

    repos = Repos(session, owner_id=owner_id)
    source, _ = await repos.sources.create_or_get_by_sha256(
        sha256=sha,
        filename=raw_name,
        ext=ext,
        size_bytes=size,
        storage_path=str(blob),
        mime=mimetypes.guess_type(raw_name)[0],
        parse_status="pending",  # 等 worker 跑 G0;解析完由流水线改为真实状态(与 CLI 导入一致)
    )
    job, job_created = await repos.jobs.create_or_get(
        dedupe_key=DEDUPE_PREFIX + sha,
        payload={"filename": raw_name, "via": "api"},
    )
    await session.commit()

    deduped = not job_created  # 同一内容此前提交过(不论任务是否已完成)
    if job.status in ("done", "running"):
        # 已完成或在途:不再入队,本次暂存目录也不会被任务用到,当场删掉
        shutil.rmtree(staging_dir, ignore_errors=True)
    else:
        # queued/failed/dead:重入队(arq _job_id 幂等挡重复;队列丢失时也借此自愈)。
        # 此时暂存文件可能被任务用到,不删;真正的孤儿由 worker 启动时的 24h 清理兜底
        try:
            await enqueue_import(
                str(staging),
                dedupe_key=DEDUPE_PREFIX + sha,
                owner_id=owner_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"任务队列不可用,请稍后重试:{type(exc).__name__}",
            ) from exc

    response.status_code = 201 if job_created else 200
    return UploadResponse(
        source_id=source.id,
        job_id=job.id,
        job_status=job.status,
        deduped=deduped,
    )


@router.post("/sources/url", response_model=UploadResponse)
async def import_from_url(
    body: UrlImportRequest,
    response: Response,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
) -> UploadResponse:
    """通过 B 站视频链接导入:两种模式可选。

    - ``mode="subtitle"``：拉 B 站 CC/AI 字幕（秒级，同步，但不稳定）
    - ``mode="whisper"``：异步——立即返回 201，worker 后台下载音频 + whisper 转写 + 帧提取（分钟级，可靠）
    字幕模式产出 markdown 后走常规 sha256/暂存/blob/enqueue/dedupe；
    whisper 模式创建 stub source + job（占位 sha），入队 ``extract_and_import_video``，
    worker 提取完 markdown 后调 ``run_import_async(source_id=...)`` 入库。
    """
    from app.core.config import get_settings
    from app.service.bilibili import BilibiliError, fetch_bilibili, sanitize_filename
    from app.service.imports.pipeline import DEDUPE_PREFIX, _store_blob
    from app.service.llm.client import LLMClient
    from app.service.llm.resolve import resolve_llm_config

    settings = get_settings()

    # 预检 API Key(没配 key 不允许导入，防止未整理内容污染知识库)
    _base, api_key, _model = await resolve_llm_config(session, owner_id)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="这个账号还没配置 API Key,请先到设置页配置后再导入",
        )

    sessdata = settings.bilibili_sessdata

    # 平台检测（B站 vs 抖音）
    from app.service.bilibili import extract_bvid
    from app.service.douyin import detect_douyin, extract_douyin_id

    is_douyin = detect_douyin(body.url)

    # 根据 mode 选择内容提取方式
    if body.mode == "whisper":
        # whisper 模式：异步——创建 stub source + job，入队后台提取，立即返回。
        if is_douyin:
            video_id = extract_douyin_id(body.url) or "unknown"
            placeholder_sha = f"video:douyin:{video_id}:whisper"
            filename = f"douyin_{video_id}.md"
            video_sessdata = ""  # 抖音不用 B站 sessdata；cookies 由 worker 从设置页读
        else:
            bvid = extract_bvid(body.url)
            if not bvid:
                raise HTTPException(
                    status_code=400,
                    detail="无法识别视频链接，请确认是 B 站(BV号/b23.tv)或抖音(v.douyin.com)链接",
                )
            placeholder_sha = f"video:{bvid}:whisper"
            filename = f"{bvid}.md"
            video_sessdata = sessdata

        repos = Repos(session, owner_id=owner_id)
        source, source_created = await repos.sources.create_or_get_by_sha256(
            sha256=placeholder_sha,
            filename=filename,
            ext=".md",
            size_bytes=0,
            storage_path="",
            mime="text/markdown",
            parse_status="pending",
        )
        # 设置视频元信息 + 进度（前端轮询读 video_stage）
        meta = {"video_url": body.url, "video_mode": "whisper", "video_stage": "排队中"}
        await repos.sources.update_parse_meta(source.id, meta)
        job, job_created = await repos.jobs.create_or_get(
            dedupe_key=DEDUPE_PREFIX + placeholder_sha,
            payload={"url": body.url, "mode": "whisper", "source_id": source.id},
            kind="import",
        )
        await session.commit()

        deduped = not job_created
        if job.status in ("done", "running"):
            pass  # 已完成或在途，不重复入队
        else:
            try:
                await enqueue_video_extract(
                    body.url,
                    mode="whisper",
                    source_id=source.id,
                    owner_id=owner_id,
                    sessdata=video_sessdata,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"任务队列不可用,请稍后重试:{type(exc).__name__}",
                ) from exc

        response.status_code = 201 if job_created else 200
        return UploadResponse(
            source_id=source.id,
            job_id=job.id,
            job_status=job.status,
            deduped=deduped,
        )

    # 字幕模式（同步快速路径：秒级拉 B 站字幕 → 写 markdown → 入库）
    if is_douyin:
        raise HTTPException(
            status_code=400,
            detail="抖音不支持字幕模式，请使用 whisper 模式",
        )
    try:
        result = await asyncio.to_thread(fetch_bilibili, body.url, sessdata=sessdata)
    except BilibiliError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="B 站接口请求失败,请稍后重试",
        )
    markdown_content = result.markdown
    video_title = result.info.title

    # 写 markdown 到暂存区(与 upload 同款:每次一个 uuid 目录)
    filename = sanitize_filename(video_title) + ".md"
    unique = uuid.uuid4().hex
    staging_dir = STAGING_ROOT / unique
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging = staging_dir / filename
    try:
        staging.write_text(markdown_content, encoding="utf-8")
    except OSError:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="暂存文件写入失败")

    sha = hashlib.sha256(staging.read_bytes()).hexdigest()
    blob = _store_blob(staging.read_bytes(), sha)

    repos = Repos(session, owner_id=owner_id)
    source, _ = await repos.sources.create_or_get_by_sha256(
        sha256=sha,
        filename=filename,
        ext=".md",
        size_bytes=staging.stat().st_size,
        storage_path=str(blob),
        mime="text/markdown",
        parse_status="pending",
    )
    job, job_created = await repos.jobs.create_or_get(
        dedupe_key=DEDUPE_PREFIX + sha,
        payload={"filename": filename, "via": "url"},
    )
    await session.commit()

    deduped = not job_created
    if job.status in ("done", "running"):
        shutil.rmtree(staging_dir, ignore_errors=True)
    else:
        try:
            await enqueue_import(
                str(staging),
                dedupe_key=DEDUPE_PREFIX + sha,
                owner_id=owner_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"任务队列不可用,请稍后重试:{type(exc).__name__}",
            ) from exc

    response.status_code = 201 if job_created else 200
    return UploadResponse(
        source_id=source.id,
        job_id=job.id,
        job_status=job.status,
        deduped=deduped,
    )


@router.get("/sources/{source_id}", response_model=SourceStatus)
async def get_source_status(
    source_id: int, session: SessionDep, owner_id: CurrentOwnerDep
) -> SourceStatus:
    # 延迟导入:与流水线共用同一份 dedupe_key 前缀,不另写一套
    from app.service.imports.pipeline import DEDUPE_PREFIX

    repos = Repos(session, owner_id=owner_id)
    source = await repos.sources.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"source 不存在:{source_id}")

    note = await repos.notes.get_by_source(source_id)
    job = await repos.jobs.get_by_dedupe_key(DEDUPE_PREFIX + source.sha256)
    runs: list[ReviewRunOut] = []
    for gate in ("G0", "G1", "G2"):
        run = await repos.review_runs.get(source_id, gate)
        if run is not None:
            runs.append(
                ReviewRunOut(
                    gate=run.gate,
                    verdict=run.verdict,
                    quality=run.quality,
                    note_type=run.note_type,
                    score=float(run.score) if run.score is not None else None,
                    action=run.action,
                    reasons=list(run.reasons or []),
                    model=run.model,
                    cost_tokens=run.cost_tokens,
                    latency_ms=run.latency_ms,
                )
            )
    meta = dict(source.parse_meta or {})
    return SourceStatus(
        id=source.id,
        filename=source.filename,
        ext=source.ext,
        size_bytes=source.size_bytes,
        mime=source.mime,
        parse_status=source.parse_status,
        message=meta.get("message"),
        warnings=list(meta.get("warnings") or []),
        g2_skipped=bool(meta.get("g2_skipped")),
        headings_rewritten=bool(meta.get("headings_rewritten")),
        video_stage=meta.get("video_stage"),
        note_id=note.id if note else None,
        created_at=source.created_at,
        job=(
            JobOut(
                id=job.id,
                status=job.status,
                attempts=job.attempts,
                last_error=job.last_error,
            )
            if job is not None
            else None
        ),
        review_runs=runs,
    )
