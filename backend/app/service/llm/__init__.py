"""LLM 层:OpenAI 兼容 adapter(AGENTS.md 决策 9)。

对外只暴露 client 与 token 估算;业务判定(G2)在上层 gate 层。
"""

from .client import (
    ChatResult,
    LLMAuthError,
    LLMCapabilities,
    LLMClient,
    LLMError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from .tokens import estimate_tokens, truncate_to_tokens

__all__ = [
    "ChatResult",
    "LLMAuthError",
    "LLMCapabilities",
    "LLMClient",
    "LLMError",
    "LLMNetworkError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMTimeoutError",
    "estimate_tokens",
    "truncate_to_tokens",
]
