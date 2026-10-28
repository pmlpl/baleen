"""repo 层:健康检查用的最小查询;业务查询后续也只允许出现在 repo/。"""

from sqlalchemy import text

from app.core.db import session_factory


async def ping_db() -> bool:
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
