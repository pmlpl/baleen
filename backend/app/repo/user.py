"""users 数据访问。只 flush,commit 由调用方负责。

**第一个用户拿 id=1**:项目单用户时期所有数据都是 owner_id=1,首个注册账号
必须继承它们。序列可能已被历史测试消耗,所以不能用自增,必须显式插 id=1
并把序列推到 1。
"""

from __future__ import annotations

from sqlalchemy import func, select, text

from app.models.user import User
from app.repo.base import Repo


class UserRepo(Repo):
    async def get_by_username(self, username: str) -> User | None:
        return await self.session.scalar(select(User).where(User.username == username))

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.id == user_id))

    async def count(self) -> int:
        return (await self.session.scalar(select(func.count()).select_from(User))) or 0

    async def create(self, *, username: str, password_hash: str) -> User:
        """建用户。表为空时显式用 id=1(继承单用户时期的存量数据)。"""
        if await self.count() == 0:
            user = User(id=1, username=username, password_hash=password_hash)
            self.session.add(user)
            await self.session.flush()
            # 序列推到 1,之后自增从 2 开始(不推会撞 id=1 主键)
            await self.session.execute(text("SELECT setval('users_id_seq', 1, true)"))
            return user
        user = User(username=username, password_hash=password_hash)
        self.session.add(user)
        await self.session.flush()
        return user
