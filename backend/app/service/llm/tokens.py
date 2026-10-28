"""Prompt 预算用的 token 估算(零依赖)。

⚠️ 这是**估算值**,不是任何模型的精确 tokenizer:用于「组装 prompt 时控制在 3000 token 内」
和冒烟脚本报长度分布。真实花费以 API 返回的 ``usage.total_tokens`` 为准。

口径:CJK 字符 ≈ 1 token/字(中文 tokenizer 普遍如此);其余字符 ≈ 3.5 字符/token。
"""

from __future__ import annotations

import math
import re

CJK_RATIO = 1.0
OTHER_CHARS_PER_TOKEN = 3.5

_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """估算文本 token 数;空串为 0。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return math.ceil(cjk * CJK_RATIO + other / OTHER_CHARS_PER_TOKEN)


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """按估算 token 截断文本,返回 (截断后文本, 是否发生截断)。

    逐段回退,避免把多字节字符切坏;截断点落在估算 token 预算内。
    """
    if max_tokens <= 0 or not text:
        return "", bool(text)
    if estimate_tokens(text) <= max_tokens:
        return text, False

    lo, hi = 0, len(text)
    while lo < hi:  # 二分找最大可容纳前缀
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo], True
