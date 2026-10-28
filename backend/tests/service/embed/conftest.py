"""embed 测试的环境注入。

backend 不能创建 .env(AGENTS §3.3),但测试需要 EMBEDDING_MODEL_PATH 才能加载 bge。
从 backend/.env.example 读出路径放进 os.environ;config 是 lru_cache,清掉让新值生效。
路径不存在则 embed 测试整体 skip(别处机器没下模型时不报红)。
"""

from __future__ import annotations

import os
from pathlib import Path

_env_example = Path(__file__).resolve().parents[4] / ".env.example"
if not os.environ.get("EMBEDDING_MODEL_PATH") and _env_example.exists():
    for _line in _env_example.read_text(encoding="utf-8").splitlines():
        if _line.startswith("EMBEDDING_MODEL_PATH="):
            os.environ["EMBEDDING_MODEL_PATH"] = _line.split("=", 1)[1].strip()
            break

# config 用 lru_cache;若已被别的测试先 import,清缓存让上面的 env 生效
try:
    from app.core.config import get_settings

    get_settings.cache_clear()
except Exception:  # noqa: BLE001 —— config 未装等情况不阻塞收集
    pass

_MODEL_PATH = os.environ.get("EMBEDDING_MODEL_PATH", "")
EMBED_AVAILABLE = bool(_MODEL_PATH) and Path(_MODEL_PATH).exists()
