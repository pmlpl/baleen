"""视觉解析(G0 装眼睛,ADR 0006):图片文件/扫描件 PDF → 视觉模型转写 markdown → block JSON。

主流 caption 路线(2026 多模态 RAG 选型):导入时视觉模型把图「看」成文字,
进正文块后走与文本笔记完全相同的 G1/G2/切块/向量化/检索流水线,后半段零改动。
引擎 = 用户自己配的 LLM(DeepSeek V4-Flash 线自带视觉),不新增依赖、不新增 key。

降级口径(绝不因识别失败让导入死掉):
- 没配 key / no_llm → router 根本不进来(no_text_expected,与旧版图片语义一致)
- 图片超限 / LLM 报错 / 返回为空 → no_text_expected + message,按「无文本层」入库
- 扫描件 PDF 逐页识别:单页失败跳过并留痕,全失败才降级 no_text_expected
- 识别结果经 G1 价值闸过滤:识别不出有价值内容自然被拒,不污染笔记库
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from app.core.config import get_settings
from app.service.llm.client import LLMClient, LLMError

from .md_parser import parse_md_text
from .types import ParseResult, ParseStatus

# 视觉 API 单图上限:超过直接不识别(节省 token,10MB 的图转写也不现实)
VISION_MAX_BYTES = 10 * 1024 * 1024

# 扫描件 PDF 栅格化参数:150dpi 足够 OCR 级清晰,又不会把页面渲成几 MB
PDF_PAGE_DPI = 150
# 超限时的降级重渲档位(从高到低试,直到塞进 VISION_MAX_BYTES)
_PDF_DPI_FALLBACKS = (110, 80)
# 单份 PDF 最多识别页数:每页一次视觉调用,不设上限会把一本 300 页扫描书烧穿
PDF_VISION_MAX_PAGES = 30

_SYSTEM = "你是笔记库的图片转写助手。把图片内容忠实转写成 Markdown,供个人知识库检索与复读。"

_PROMPT = (
    "把这张图片的内容完整转写成 Markdown:\n"
    "- 图里有文字就全部转写,保持原有的标题层级、列表、表格结构(表格用 Markdown 表格)\n"
    "- 是手写笔记/板书也照实转写,看不清的字用〔?〕占位,不要编造\n"
    "- 图里没有文字(纯照片/插画/界面图)就详细描述它展示的内容\n"
    "- 只输出 Markdown 正文,不要任何解释、不要代码围栏包裹整体"
)


def _transcribe_image(raw: bytes, mime: str, *, client: LLMClient) -> str:
    """单张图字节 → 视觉模型转写文本(已 strip)。

    LLMError 原样上抛由调用方决定降级口径;空 content 返回空串(推理模型烧 token 的口径)。
    """
    b64 = base64.b64encode(raw).decode("ascii")
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        },
    ]
    chat = client.chat(messages, temperature=0.0, max_tokens=get_settings().llm_max_output_tokens)
    return chat.content.strip()


def parse_image_vision(path: Path | str, *, client: LLMClient) -> ParseResult:
    """单张图片 → 视觉模型转写 → ParseResult;失败收敛为 no_text_expected,不抛。"""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return ParseResult(
            parse_status=ParseStatus.ERROR,
            message=f"图片读取失败:{type(exc).__name__}"[:300],
            meta={"engine": "vision", "chars": 0},
        )
    if len(raw) > VISION_MAX_BYTES:
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message=f"图片超过 {VISION_MAX_BYTES // (1024 * 1024)} MB,暂不做视觉识别",
            meta={"engine": "vision", "chars": 0},
        )
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    try:
        markdown = _transcribe_image(raw, mime, client=client)
    except LLMError as exc:
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message=f"视觉识别失败({type(exc).__name__}),按无文本层处理;可检查设置页模型是否支持视觉",
            meta={"engine": "vision", "chars": 0, "vision_error": str(exc)[:200]},
        )
    if not markdown:
        # reasoning 烧满 token 的空 content 口径与 summarize 同款:视为失败,不建正文
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message="视觉识别没有返回内容,按无文本层处理",
            meta={"engine": "vision", "chars": 0},
        )
    doc_blocks, chars = parse_md_text(markdown)
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": doc_blocks},
        # 识别可能有误差:让主人知道这份正文的来源与可信度
        warnings=["图片内容由 AI 视觉识别转写,可能有误差;可在笔记编辑器里修正"],
        meta={"engine": f"vision({client.model})", "chars": chars, "vision_chars": chars},
    )


def _render_page_png(page: object) -> bytes | None:
    """单页 → PNG 字节:按 DPI 档位从高到低试,直到塞进 VISION_MAX_BYTES。

    只渲缩小不改画质参数——降 DPI 是唯一能让体积单调下降的旋钮。
    """
    for dpi in (PDF_PAGE_DPI, *_PDF_DPI_FALLBACKS):
        png = page.get_pixmap(dpi=dpi).tobytes("png")  # type: ignore[attr-defined]
        if len(png) <= VISION_MAX_BYTES:
            return png
    return None


def parse_scanned_pdf(path: Path | str, *, client: LLMClient) -> ParseResult:
    """扫描件 PDF(无文本层)→ 逐页栅格化 → 视觉转写 → ParseResult。

    每页一次视觉调用(串行);单页失败只跳过该页并留痕,全失败才降级 no_text_expected。
    页数上限 ``PDF_VISION_MAX_PAGES``:超出的页不识别并明确警告(不静默截断)。
    """
    import pymupdf  # 局部导入:纯图片视觉路径不必依赖 pymupdf

    p = Path(path)
    try:
        doc = pymupdf.open(p)
    except Exception as exc:  # pymupdf 对损坏文件抛的异常类型不统一
        return ParseResult(
            parse_status=ParseStatus.ERROR,
            message=f"PDF 读取失败:{type(exc).__name__}"[:300],
            meta={"engine": "pdf-vision", "chars": 0},
        )

    total_pages = doc.page_count
    pages_to_read = min(total_pages, PDF_VISION_MAX_PAGES)
    page_texts: list[str] = []
    failed_pages: list[int] = []
    overlimit_pages: list[int] = []
    chars = 0

    for pno in range(pages_to_read):
        png = _render_page_png(doc[pno])
        if png is None:
            overlimit_pages.append(pno + 1)
            continue
        try:
            markdown = _transcribe_image(png, "image/png", client=client)
        except LLMError:
            failed_pages.append(pno + 1)
            continue
        if not markdown:
            failed_pages.append(pno + 1)
            continue
        chars += len(markdown)
        # 页码标记用**普通段落**而不是 markdown 标题:
        # ① 标题会让 pipeline._title 把「第 1 页」当成笔记标题;
        # ② 30 页的扫描书会往目录里塞 30 条「第 N 页」,真内容标题反而被淹。
        # 单页 PDF 不加标记(纯噪音)。
        page_texts.append(markdown if total_pages == 1 else f"—— 第 {pno + 1} 页 ——\n\n{markdown}")

    if not page_texts:
        doc.close()
        reasons = []
        if overlimit_pages:
            reasons.append(f"{len(overlimit_pages)} 页渲染后超过 10 MB")
        if failed_pages:
            reasons.append(f"{len(failed_pages)} 页识别失败")
        detail = (";" + "、".join(reasons)) if reasons else ""
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message=(
                f"扫描件 PDF 的 {pages_to_read} 页都没能识别出内容{detail};"
                "按无文本层处理,可检查设置页模型是否支持视觉"
            ),
            meta={
                "engine": "pdf-vision",
                "chars": 0,
                "pages": total_pages,
                "failed_pages": failed_pages,
                "overlimit_pages": overlimit_pages,
            },
        )

    doc_blocks, _ = parse_md_text("\n\n".join(page_texts))
    warnings = ["扫描件内容由 AI 视觉识别转写,可能有误差;可在笔记编辑器里修正"]
    if failed_pages:
        shown = "、".join(str(n) for n in failed_pages[:10])
        more = "等" if len(failed_pages) > 10 else ""
        warnings.append(f"第 {shown}{more} 页识别失败,已跳过")
    if overlimit_pages:
        warnings.append(f"第 {len(overlimit_pages)} 页渲染后超过体积上限,已跳过")
    if total_pages > PDF_VISION_MAX_PAGES:
        warnings.append(
            f"PDF 共 {total_pages} 页,只识别了前 {PDF_VISION_MAX_PAGES} 页"
            f"(上限 PDF_VISION_MAX_PAGES)"
        )

    doc.close()
    return ParseResult(
        parse_status=ParseStatus.OK,
        blocks={"type": "doc", "content": doc_blocks},
        warnings=warnings,
        meta={
            "engine": f"pdf-vision({client.model})",
            "chars": chars,
            "vision_chars": chars,
            "pages": total_pages,
            "transcribed_pages": len(page_texts),
            "failed_pages": failed_pages,
            "overlimit_pages": overlimit_pages,
        },
    )


__all__ = [
    "PDF_VISION_MAX_PAGES",
    "VISION_MAX_BYTES",
    "parse_image_vision",
    "parse_scanned_pdf",
]
