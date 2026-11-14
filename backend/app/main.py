"""应用入口:在 backend/ 目录下 `uv run fastapi dev` 启动(默认 127.0.0.1:8000)。"""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.charter import router as charter_router
from app.api.health import router as health_router
from app.api.library import router as library_router
from app.api.profile import router as profile_router
from app.api.qa import router as qa_router
from app.api.settings import router as settings_router
from app.api.upload import router as upload_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.redis import create_redis_client

log = logging.getLogger(__name__)


def _warmup_embedding_model() -> None:
    """后台线程预加载嵌入模型:消除后端重启后第一问的冷启动等待(3~8s)。

    尽力而为:任何失败(路径没配/无 GPU/模型缺失)都不捕获也行——线程异常只打
    traceback 到日志,不影响启动;首次真实调用会照旧走按需加载。
    检索本身毫秒级,慢的从来只是这个冷启动。
    """
    import time

    from app.service.embed import embed_query, load_seconds

    t0 = time.perf_counter()
    embed_query("预热")  # 模型加载 + 一次真实推理,把 CUDA 上下文也焐热
    log.info(
        "embedding warmup done in %.1fs (model load %.1fs)",
        time.perf_counter() - t0,
        load_seconds() or -1.0,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = create_redis_client()
    if get_settings().embed_warmup_on_startup:
        threading.Thread(target=_warmup_embedding_model, daemon=True, name="embed-warmup").start()
    yield
    await app.state.redis.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="baleen", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(library_router)
    app.include_router(qa_router)
    app.include_router(settings_router)
    app.include_router(charter_router)
    app.include_router(profile_router)
    app.include_router(upload_router)
    return app


app = create_app()
