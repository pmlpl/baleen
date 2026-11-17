"""轻量附件解析 API(📎 统一入口,不入库)。

- POST /api/parse —— multipart 单文件 → G0 ``parse_file``(复用导入同款解析栈)
  → ``{status, text, images, warnings, message, mode, truncated}``

分层(大文件不做硬截断,业界 map-reduce 路线):
- 正文 ≤ ``FILE_FULL_CHARS`` → 全文直塞(mode=full)
- 超出 → ``file_analyzer`` 分块并行提取 → 结构化报告(mode=analyzed)
- 文内图片(md/html 的 data URI 与本地图)字节随响应回传,前端转视觉通道缩图

与导入链路(``/api/sources``)的差异:不建 source/job、不入库 —— 📎 的语义是
临时参考文件(import-vs-attachment 约定),解析产物只活在前端当轮。
没配 key 不挡 415/400:文本格式照常解析,只有视觉与分析才需要 client。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import CurrentOwnerDep, SessionDep
from app.api.upload import ALLOWED_EXTS  # 上传闸门语义只在 G0 一处,直接复用组装结果
from app.service.llm import LLMClient

router = APIRouter(prefix="/api", tags=["parse"])

MAX_PARSE_BYTES = 10 * 1024 * 1024  # 单文件上限(docx/pdf 简历类常见 1~5MB)
BODY_LIMIT_BYTES = 12 * 1024 * 1024  # 请求体上限(multipart 有边界开销,比文件上限松一档)
FILE_FULL_CHARS = 40_000  # 正文全文直塞上限;超出走 file_analyzer 分析(报告另有上限)
MAX_IMAGES = 4  # 与 ChatRequest.images max_length、前端 ATTACH_IMAGE_MAX_COUNT 三方对齐
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 单张图字节上限,同前端 ATTACH_IMAGE_MAX_BYTES

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_mime(raw: bytes) -> str:
    """图片字节嗅探 MIME( sink 回调只有 bytes+alt,拿不到扩展名);认不出返回 octet-stream。"""
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return mime
    return "application/octet-stream"


class ParseImageOut(BaseModel):
    """文内提取的图片:字节转 data URL 回传,前端接进视觉通道。"""

    name: str
    mime: str
    data_url: str


class ParseAttachmentResponse(BaseModel):
    """POST /api/parse 响应。

    status ≠ ok 时 text 为空、message 带用户可读说明(如 .doc 的转换提示);
    mode=analyzed 表示正文经 AI 分析提炼(丢细节,truncated 永不与 analyzed 同真)。
    """

    status: str
    text: str = ""
    mode: Literal["full", "analyzed"] = "full"
    images: list[ParseImageOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None
    truncated: bool = False


async def guard_body_size(request: Request) -> None:
    """请求体上限先挡一轮:依赖只声明 Request,在解析 multipart 之前执行。"""
    raw = request.headers.get("content-length", "")
    if raw.isdigit() and int(raw) > BODY_LIMIT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"请求体超过上限:{BODY_LIMIT_BYTES // (1024 * 1024)} MB",
        )


@router.post("/parse", response_model=ParseAttachmentResponse)
async def parse_attachment(
    _: Annotated[None, Depends(guard_body_size)],
    file: UploadFile,
    session: SessionDep,
    owner_id: CurrentOwnerDep,
) -> ParseAttachmentResponse:
    from app.service.llm.resolve import resolve_llm_config  # 延迟导入:api 进程不因
    from app.service.parse import parse_file  # import 本模块而加载解析栈/torch
    from app.service.parse.blocks import flatten_text  # (upload.py 同款纪律)
    from app.service.qa.file_analyzer import REPORT_MAX_CHARS, FileTooLargeError, analyze

    raw_name = Path(file.filename or "").name
    if not raw_name:
        raise HTTPException(status_code=422, detail="缺少文件名")
    ext = Path(raw_name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=415, detail=f"暂不支持的格式:{ext or '(无扩展名)'}")

    raw = await file.read()  # 上限 10MB,一次读进内存可接受
    await file.close()
    if len(raw) > MAX_PARSE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小上限:{MAX_PARSE_BYTES // (1024 * 1024)} MB",
        )

    # 没配 key 降级 client=None:文本格式照常解析,只有视觉/扫描件/大文件分析不可用
    base_url, api_key, model = await resolve_llm_config(session, owner_id)
    client = LLMClient(base_url=base_url, api_key=api_key, model=model) if api_key else None

    # 收集文内图片字节(仅 md/html 的 sink 生效);不入库,回传前端走视觉通道
    collected: list[tuple[str, bytes]] = []

    def sink(data: bytes, alt: str) -> str:
        collected.append((alt, data))
        return f"preview:{len(collected) - 1}"  # 假 key:本接口不返回 blocks,没人请求 /api/media

    warnings: list[str] = []
    truncated = False
    mode: Literal["full", "analyzed"] = "full"
    with tempfile.TemporaryDirectory(prefix="baleen-parse-") as tmp:
        staging = Path(tmp) / raw_name
        staging.write_bytes(raw)
        # 同步重活(扫描件逐页视觉可达几十秒)进线程池,不堵事件循环
        result = await asyncio.to_thread(parse_file, staging, client=client, media_sink=sink)

    if result.parse_status != "ok":
        return ParseAttachmentResponse(
            status=str(result.parse_status), warnings=result.warnings, message=result.message
        )
    text = flatten_text(result.blocks)
    warnings.extend(result.warnings)

    if len(text) <= FILE_FULL_CHARS:
        pass  # 全文直塞
    elif client is None:
        text = text[:FILE_FULL_CHARS]
        truncated = True
        warnings.append("正文超长且未配置 API Key,已截断;配置后可自动分析全文")
    else:
        try:
            text = await analyze(text, client)
        except FileTooLargeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        mode = "analyzed"
        warnings.append("大文件已由 AI 分析提炼,细节可能省略;需要原文请拆分后分次上传")
        if len(text) > REPORT_MAX_CHARS:
            text = text[:REPORT_MAX_CHARS]
            truncated = True

    images: list[ParseImageOut] = []
    for alt, data in collected:
        if len(images) >= MAX_IMAGES:
            warnings.append(f"图片超过 {MAX_IMAGES} 张,只保留前 {MAX_IMAGES} 张")
            break
        if len(data) > MAX_IMAGE_BYTES:
            warnings.append("有图片超过 5MB,已跳过")
            continue
        mime = _sniff_mime(data)
        if mime == "application/octet-stream":
            continue  # 嗅探不出的不硬塞视觉通道
        b64 = base64.b64encode(data).decode("ascii")
        images.append(ParseImageOut(name=alt, mime=mime, data_url=f"data:{mime};base64,{b64}"))

    return ParseAttachmentResponse(
        status="ok",
        text=text,
        mode=mode,
        images=images,
        warnings=warnings,
        truncated=truncated,
    )
