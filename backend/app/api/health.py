"""GET /healthz:进程存活 + db/redis 探活。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.redis import ping as ping_redis
from app.repo.health import ping_db

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: str
    db: str
    redis: str


@router.get("/healthz")
async def healthz(request: Request) -> HealthStatus:
    db_ok = await ping_db()
    redis_ok = await ping_redis(request.app.state.redis)
    ok = db_ok and redis_ok
    return HealthStatus(
        status="ok" if ok else "error",
        db="ok" if db_ok else "error",
        redis="ok" if redis_ok else "error",
    )
