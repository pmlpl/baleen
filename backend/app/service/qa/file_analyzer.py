"""大文件分层分析(map-reduce):正文超「全文直塞」阈值时,分块提炼 → 合并成结构化报告。

为什么存在:📎 附件全文会进 user 消息的 ``llm_content``,本会话之后每轮都重放
(``app/api/qa.py``),硬塞大文件会永久撑爆上下文;硬截断又丢细节。业界 third path
(map-reduce summarization):每块并行提取关键信息 → reduce 合并成一份报告。

代价如实告知用户:报告丢细节(问「第三段原文写了什么」答不了)——细节级问答
留给二期的附件临时 RAG。
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.service.llm import LLMClient, LLMError

# 分块参数:块长与 AttachmentInput.text 上限同量级;重叠防止关键句正好被切在边界
CHUNK_CHARS = 20_000
CHUNK_OVERLAP = 500
# 块数上限:20 块 ≈ 39 万字符正文,再大就该用户自己拆分上传,而不是默默烧 token
MAX_CHUNKS = 20
# 分析报告上限(与全量直塞分界 40000 错开:报告一定明显小于全文)
REPORT_MAX_CHARS = 20_000
# 并行提取并发数:过高会撞网关限流
CONCURRENCY = 4
# 单块提取失败时的降级保留长度(原文头部,比整块信息全丢好)
_CHUNK_FALLBACK_CHARS = 3_000

_SYSTEM = "你是文件分析助手,负责把超长文档片段提炼成结构化信息,供后续问答引用。"

_MAP_PROMPT = (
    "以下是一份长文档的第 {i}/{n} 部分(自动切分,可能从句中开始或结束)。\n"
    "提取这部分的关键信息,输出为简洁的结构化要点:\n"
    "- 事实、数字、日期、人名/机构名、联系方式原样保留,不许编造\n"
    "- 保留章节结构与层级(用 Markdown 标题/列表)\n"
    "- 省略寒暄、重复排版、纯装饰内容\n"
    "只输出要点正文,不要评论、不要代码围栏。"
)

_REDUCE_PROMPT = (
    "下面是对同一份长文档各部分分别提取的要点清单,请合并成一份完整的结构化报告:\n"
    "- 去重、保持章节顺序与层级\n"
    "- 事实/数字/名称原样保留,不许编造\n"
    "- 总长度控制在 {limit} 字符以内\n"
    "只输出报告正文,不要评论。\n\n"
)


class FileTooLargeError(Exception):
    """正文块数超过 MAX_CHUNKS:让用户拆分上传,而不是默默烧 token。"""


def split_chunks(text: str) -> list[str]:
    """定长滑窗分块:步进 = 块长 - 重叠;尾部不足一块时直接收尾。"""
    step = CHUNK_CHARS - CHUNK_OVERLAP
    chunks: list[str] = []
    start = 0
    while True:
        chunks.append(text[start : start + CHUNK_CHARS])
        if start + CHUNK_CHARS >= len(text):
            return chunks
        start += step


def _chat(client: LLMClient, prompt: str) -> str:
    """单次 LLM 调用(同步,调用方决定放不放线程池);空返回当失败处理。"""
    chat = client.chat(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=get_settings().llm_max_output_tokens,
    )
    return chat.content.strip()


async def analyze(text: str, client: LLMClient) -> str:
    """大文件正文 → 结构化分析报告。

    块数超限抛 ``FileTooLargeError``(接口层转 422 提示拆分);
    单块 LLM 失败降级保留原文头部,不整体失败;reduce 失败硬截拼接结果。
    """
    chunks = split_chunks(text)
    if len(chunks) > MAX_CHUNKS:
        raise FileTooLargeError(
            f"文件正文约 {len(text)} 字符,超出单次附件分析上限"
            f"(约 {(CHUNK_CHARS - CHUNK_OVERLAP) * MAX_CHUNKS} 字符),请拆分后分次上传"
        )

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def map_one(index: int, chunk: str) -> str:
        prompt = _MAP_PROMPT.format(i=index + 1, n=len(chunks))
        async with semaphore:
            try:
                return await asyncio.to_thread(_chat, client, prompt)
            except LLMError:
                return chunk[:_CHUNK_FALLBACK_CHARS]

    parts = await asyncio.gather(*(map_one(i, c) for i, c in enumerate(chunks)))

    body = "\n\n".join(
        f"## 第 {i + 1} 部分\n\n{part}" for i, part in enumerate(parts) if part
    )
    if len(body) <= REPORT_MAX_CHARS:
        return body

    # 要点总量超限 → reduce 合并一轮;reduce 再失败就硬截(不整体失败)
    try:
        merged = await asyncio.to_thread(_chat, client, _REDUCE_PROMPT.format(limit=REPORT_MAX_CHARS) + body)
    except LLMError:
        merged = body
    return merged[:REPORT_MAX_CHARS]
