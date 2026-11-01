"""视觉解析(G0 装眼睛,ADR 0006):图片文件 → 视觉模型转写 markdown → block JSON。

主流 caption 路线(2026 多模态 RAG 选型):导入时视觉模型把图「看」成文字,
进正文块后走与文本笔记完全相同的 G1/G2/切块/向量化/检索流水线,后半段零改动。
引擎 = 用户自己配的 LLM(DeepSeek V4-Flash 线自带视觉),不新增依赖、不新增 key。

降级口径(绝不因识别失败让导入死掉):
- 没配 key / no_llm → router 根本不进来(no_text_expected,与旧版图片语义一致)
- 图片超限 / LLM 报错 / 返回为空 → no_text_expected + message,按「无文本层」入库
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

_SYSTEM = (
    "你是笔记库的图片转写助手。把图片内容忠实转写成 Markdown,供个人知识库检索与复读。"
)

_PROMPT = (
    "把这张图片的内容完整转写成 Markdown:\n"
    "- 图里有文字就全部转写,保持原有的标题层级、列表、表格结构(表格用 Markdown 表格)\n"
    "- 是手写笔记/板书也照实转写,看不清的字用〔?〕占位,不要编造\n"
    "- 图里没有文字(纯照片/插画/界面图)就详细描述它展示的内容\n"
    "- 只输出 Markdown 正文,不要任何解释、不要代码围栏包裹整体"
)


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
    try:
        chat = client.chat(
            messages, temperature=0.0, max_tokens=get_settings().llm_max_output_tokens
        )
    except LLMError as exc:
        return ParseResult(
            parse_status=ParseStatus.NO_TEXT_EXPECTED,
            message=f"视觉识别失败({type(exc).__name__}),按无文本层处理;可检查设置页模型是否支持视觉",
            meta={"engine": "vision", "chars": 0, "vision_error": str(exc)[:200]},
        )
    markdown = chat.content.strip()
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


__all__ = ["VISION_MAX_BYTES", "parse_image_vision"]
