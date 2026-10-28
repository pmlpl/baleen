"""向量化器单测(AGENTS §5 决策4 / ADR 0002)。

需要本地 bge-large-zh-v1.5 模型;路径由 conftest 从 .env.example 注入。
无模型时整体 skip。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

# conftest 已把 EMBEDDING_MODEL_PATH 从 .env.example 注入 os.environ
_MP = os.environ.get("EMBEDDING_MODEL_PATH", "")
EMBED_AVAILABLE = bool(_MP) and Path(_MP).exists()
pytestmark = pytest.mark.skipif(
    not EMBED_AVAILABLE, reason="EMBEDDING_MODEL_PATH 未配置或模型不存在"
)


def test_query_dim_and_unit_norm():
    from app.service.embed import embed_query

    v = embed_query("什么是闭包")
    assert v.shape == (1024,)
    assert abs(np.linalg.norm(v) - 1.0) < 1e-3, "模型自带 Normalize,输出应已单位化"


def test_passage_dim_and_unit_norm():
    from app.service.embed import embed_passage

    vs = embed_passage(["闭包是函数与其词法环境的组合。", "内存泄漏常发生在闭包持有引用时。"])
    assert vs.shape == (2, 1024)
    norms = np.linalg.norm(vs, axis=1)
    assert np.all(np.abs(norms - 1.0) < 1e-3)


def test_asymmetry_query_has_prefix():
    """非对称检索:query 加前缀,passage 不加 → 同一文本两者向量不同。"""
    from app.service.embed import embed_passage, embed_query

    q = embed_query("闭包")
    p = embed_passage(["闭包"])[0]
    assert not np.allclose(q, p, atol=1e-6), "query 前缀应改变向量(非对称)"


def test_count_tokens_uses_real_tokenizer():
    from app.service.embed import count_tokens

    # 中文 1 汉字 ≈ 1 token,但含 [CLS]/[SEP] 等特殊 token,故略大于字数
    n = count_tokens("中文文档解析是知识库的第一道闸门。")
    assert 10 < n < 30
