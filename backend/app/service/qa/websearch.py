"""Tavily 网页搜索(工具链最小实现:库外问题的联网兜底)。

选型记录(ADR 0005 §5 + 主人拍板 D4):Tavily 免费档 1000 次/月,为 LLM/RAG 而生
(返回干净正文而非链接列表);SearXNG 自托管为备选。失败向上抛,调用方兜底。
"""

from __future__ import annotations

import json
import urllib.request

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def search_web(
    query: str, *, api_key: str, max_results: int = 5, timeout: float = 30
) -> list[dict[str, str]]:
    """调 Tavily 搜索,返回 ``[{title, url, content}]``;失败向上抛(调用方兜底)。"""
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
            "content": (item.get("content") or "")[:1500],
        }
        for item in data.get("results") or []
    ]
