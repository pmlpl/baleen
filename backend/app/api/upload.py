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

import hashlib
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentOwnerDep, get_session
from app.repo import Repos
from app.service.parse.router import IMAGE_EXTS, PARSERS, UNSUPPORTED_MESSAGES
from app.worker import STAGING_ROOT, enqueue_import

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
BODY_LIMIT_BYTES = 55 * 1024 * 1024
READ_CHUNK = 1024 * 1024
ALLOWED_EXTS = set(PARSERS) | set(IMAGE_EXTS) | set(UNSUPPORTED_MESSAGES)

router = APIRouter(prefix="/api", tags=["sources"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class UploadResponse(BaseModel):
    source_id: int
    job_id: int
    job_status: str
    deduped: bool


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
    no_llm: Annotated[
        bool, Query(description="跳过 G2(不写 G2 判定行,parse_meta 记 g2_skipped)")
    ] = False,
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

    # 预检:要跑 G2 却没配 API Key → 当场提示去设置页,别让任务在队列里静默降级
    if not no_llm:
        _base, api_key, _model = await resolve_llm_config(session, owner_id)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="这个账号还没配置 API Key,请先到设置页配置,或勾选「跳过 AI 判定」后再上传",
            )

    # 流式落暂存区(保留原文件名/扩展名,文件名本身是判定特征):边读边算 sha256、边限大小;
    # blob 仍由流水线存,这里先存一份让 source.storage_path 立即有效
    suffix = Path(raw_name).suffix
    staging = STAGING_ROOT / f"{uuid.uuid4().hex}_{Path(raw_name).stem[:190]}{suffix}"
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
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
    except HTTPException:
        staging.unlink(missing_ok=True)
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
        parse_status="error",  # 占位;G0 完成后由流水线改为真实状态(与 CLI 导入一致)
    )
    job, job_created = await repos.jobs.create_or_get(
        dedupe_key=DEDUPE_PREFIX + sha,
        payload={"filename": raw_name, "no_llm": no_llm, "via": "api"},
    )
    await session.commit()

    deduped = not job_created  # 同一内容此前提交过(不论任务是否已完成)
    if job.status in ("done", "running"):
        # 已完成或在途:不再入队,本次暂存文件也不会被任务用到,当场删掉
        staging.unlink(missing_ok=True)
    else:
        # queued/failed/dead:重入队(arq _job_id 幂等挡重复;队列丢失时也借此自愈)。
        # 此时暂存文件可能被任务用到,不删;真正的孤儿由 worker 启动时的 24h 清理兜底
        try:
            await enqueue_import(
                str(staging),
                dedupe_key=DEDUPE_PREFIX + sha,
                no_llm=no_llm,
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
