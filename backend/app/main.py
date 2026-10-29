"""应用入口:在 backend/ 目录下 `uv run fastapi dev` 启动(默认 127.0.0.1:8000)。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.library import router as library_router
from app.api.qa import router as qa_router
from app.api.settings import router as settings_router
from app.api.upload import router as upload_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.redis import create_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = create_redis_client()
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
    app.include_router(library_router)
    app.include_router(qa_router)
    app.include_router(settings_router)
    app.include_router(upload_router)
    return app


app = create_app()
