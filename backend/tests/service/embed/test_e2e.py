"""端到端:block JSON → 切块(真 token 计数)→ 向量化 → 维度/长度断言。"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from app.service.chunk import chunk

# conftest 已把 EMBEDDING_MODEL_PATH 从 .env.example 注入 os.environ
_MP = os.environ.get("EMBEDDING_MODEL_PATH", "")
EMBED_AVAILABLE = bool(_MP) and Path(_MP).exists()
pytestmark = pytest.mark.skipif(
    not EMBED_AVAILABLE, reason="EMBEDDING_MODEL_PATH 未配置或模型不存在"
)


def test_markdown_to_chunks_to_embeddings():
    from app.service.embed import count_tokens, embed_passage

    doc = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [{"type": "text", "text": "闭包的坑"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "闭包是函数与其词法环境的组合。" * 20}],
            },
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "内存泄漏"}],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "内存泄漏常发生在闭包持有不再需要的引用时。" * 15}
                ],
            },
        ],
    }
    r = chunk(doc, token_counter=count_tokens)
    # 硬指标:用真 tokenizer 也不超限、不截断
    assert r.metrics["chunks_over_limit"] == 0
    assert r.metrics["chunks_truncated"] == 0

    embeddable = [c for c in r.chunks if not c.is_parent and not c.meta.get("skipped_embed")]
    assert len(embeddable) >= 2
    vecs = embed_passage([c.text for c in embeddable])
    assert vecs.shape == (len(embeddable), 1024)
    norms = np.linalg.norm(vecs, axis=1)
    assert np.all(np.abs(norms - 1.0) < 1e-3), "所有子块向量应已单位化"
