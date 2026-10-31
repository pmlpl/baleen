"""向量化器(AGENTS.md §5 决策4、ADR 0002/0004)。

bge-large-zh-v1.5(1024 维,本地)。非对称检索:query 加前缀、passage 不加。
device 自动降级(cuda if available else cpu)——答辩机器可能没有 GPU。
模型自带 Normalize → 输出已 L2 归一化,直接用内积,不再重复归一化。
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

log = logging.getLogger(__name__)

# 非对称检索:query 必须加这个前缀,passage 不加(AGENTS §5)
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

_model: SentenceTransformer | None = None
_device: str | None = None
_load_seconds: float | None = None
# 并发首调(预热线程 + 同时进来的请求)只加载一次:双份加载会翻倍吃显存
_load_lock = threading.Lock()


def _choose_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def get_device() -> str:
    global _device
    if _device is None:
        _device = _choose_device()
    return _device


def load_seconds() -> float | None:
    """模型加载耗时(秒);未加载过返回 None。"""
    return _load_seconds


def _load() -> SentenceTransformer:
    global _model, _load_seconds
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:  # 等锁期间别人已加载好
            return _model
        return _load_unlocked()


def _load_unlocked() -> SentenceTransformer:
    global _model, _load_seconds
    path = get_settings().embedding_model_path
    if not path:
        raise RuntimeError("EMBEDDING_MODEL_PATH 未配置;请在 backend/.env 里设置(见 .env.example)")
    dev = get_device()
    log.info("embedding device: %s", dev)
    t0 = time.perf_counter()
    _model = SentenceTransformer(path, device=dev)
    _load_seconds = time.perf_counter() - t0
    log.info("embedding model loaded in %.1fs", _load_seconds)
    return _model


def count_tokens(text: str) -> int:
    """用 bge tokenizer 实算 token 数(ADR 0004 §6:chunks_truncated 必须用真 tokenizer)。"""
    tokenizer = _load().tokenizer
    return len(tokenizer(text, truncation=False)["input_ids"])


def embed_passage(texts: list[str], batch_size: int | None = None) -> np.ndarray:
    """段落向量化(不加前缀)。输出已 L2 归一化(模型自带 Normalize)。"""
    model = _load()
    bs = batch_size or (32 if get_device() == "cuda" else 8)
    return model.encode(
        texts,
        batch_size=bs,
        normalize_embeddings=False,  # 模型自带 Normalize,不再重复归一化
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def embed_query(text: str) -> np.ndarray:
    """查询向量化(加前缀,非对称检索)。"""
    return embed_passage([QUERY_PREFIX + text])[0]
