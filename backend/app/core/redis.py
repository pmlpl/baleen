"""Redis:客户端创建与探活;实例生命周期由 main.py 的 lifespan 管理。"""

from redis.asyncio import Redis

from app.core.config import get_settings


def create_redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def ping(client: Redis) -> bool:
    try:
        return bool(await client.ping())
    except Exception:
        return False
