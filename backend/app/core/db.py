"""数据库:全局唯一的 async engine / session factory,导入时创建、此后不再变。"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)
