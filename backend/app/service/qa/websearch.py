"""联网搜索两级链:Tavily 主力(免费)→ DeepSeek Search 兜底(付费)。

选型记录(ADR 0005 §5 + 主人拍板 D4):Tavily 免费档 1000 次/月,为 LLM/RAG 而生
(返回干净正文而非链接列表)。2026-11-23 免费配额用尽(HTTP 432),主人拍板加
**DeepSeek 原生搜索 API 兜底**:Tavily 任何失败(432 配额/超时/key 失效)自动降级
调 DeepSeek(用 owner 自己的 llm key,与问答同一网关);Tavily 恢复后**零配置变更**
自动回归免费主力(成功即优先,降级只在失败时发生)。

降级留痕(凡失败必留痕):降级原因进 logger(warning 级,配额恢复与否看日志就知道:
恢复后不再出现降级告警),并写 ``meta_out["fallback"]`` 给调用方透出到响应可见处
(agent 步骤留痕/流式 status)。

非 DeepSeek LLM 用户(base_url 非 deepseek 网关/未配 key):兜底不可用,Tavily
失败直接向上抛(调用方走自答口径)。两级全失败同样向上抛。
"""

from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"

# 与 Tavily 主力路径同一截断口径(对齐喂给 answerer 的内容形态)
_CONTENT_CHARS = 1500


def _reason(exc: Exception) -> str:
    """失败原因的短描述(不进 key、不拼异常全文,只类型+HTTP 码)。"""
    code = getattr(exc, "code", None)  # urllib.error.HTTPError.code
    return f"HTTP {code}" if code else type(exc).__name__


def _search_tavily(
    query: str, *, api_key: str, max_results: int, timeout: float
) -> list[dict[str, str]]:
    """调 Tavily 搜索,返回 ``[{title, url, content}]``;失败向上抛。"""
    body = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        TAVILY_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "content": (item.get("content") or "")[:_CONTENT_CHARS],
        }
        for item in data.get("results") or []
    ]


def _search_deepseek(
    query: str, *, base_url: str, api_key: str, max_results: int, timeout: float
) -> list[dict[str, str]]:
    """DeepSeek 原生搜索(2026-11-23 探针实测格式,别猜):

    ``POST {base}/v1/tools/search`` body ``{"query": ...}``
    Bearer 认证 → ``results[]`` 各含 title/url/summary(整页正文 markdown,
    为主内容字段)/snippet/content。base_url 可能带 /v1 也可能不带(归一化两种)。
    """
    root = base_url.rstrip("/")
    if not root.endswith("/v1"):
        root += "/v1"
    req = urllib.request.Request(
        root + "/tools/search",
        data=json.dumps({"query": query}, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "content": (
                item.get("summary") or item.get("content") or item.get("snippet") or ""
            )[:_CONTENT_CHARS],
        }
        for item in (data.get("results") or [])[:max_results]
    ]


def search_web(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    timeout: float = 30,
    fallback_base_url: str | None = None,
    fallback_api_key: str | None = None,
    fallback_timeout: float = 45,
    meta_out: dict | None = None,
) -> list[dict[str, str]]:
    """两级搜索链,返回 ``[{title, url, content}]``。

    1. Tavily 主力(免费):成功即返回——**配额恢复后零配置自动回归**。
    2. Tavily 任何失败 → DeepSeek Search 兜底(付费,owner 的 llm key;
       base_url 非 deepseek 网关或未配 key = 兜底不可用,直接抛)。
    3. 两级全失败向上抛(调用方走「没找到」/自答口径)。

    降级发生时:logger 告警 + ``meta_out["fallback"]`` 写原因(如
    ``"Tavily HTTP 432→DeepSearch 兜底"``),调用方可透出到 agent 步骤/流式 status。
    """
    try:
        return _search_tavily(query, api_key=api_key, max_results=max_results, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 —— 任何失败(432/超时/key 失效)都降级
        deepseek = bool(fallback_base_url) and "deepseek" in (fallback_base_url or "").lower()
        if not (deepseek and fallback_api_key):
            logger.warning(
                "Tavily 搜索失败(%s),DeepSearch 兜底不可用(LLM 网关非 DeepSeek 或未配 key)",
                _reason(exc),
            )
            raise
        note = f"Tavily {_reason(exc)}→DeepSearch 兜底"
        try:
            results = _search_deepseek(
                query,
                base_url=fallback_base_url,
                api_key=fallback_api_key,
                max_results=max_results,
                timeout=fallback_timeout,
            )
        except Exception as exc2:  # noqa: BLE001 —— 兜底也败,交调用方自答
            logger.warning("联网搜索两级均失败:Tavily(%s)+ DeepSearch(%s)", _reason(exc), _reason(exc2))
            raise
        logger.warning("联网搜索降级:%s,成功(%d 条)", note, len(results))
        if meta_out is not None:
            meta_out["fallback"] = note
        return results
