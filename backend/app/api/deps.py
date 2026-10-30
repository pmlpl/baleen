"""API 层共享依赖。

- ``SessionDep``:数据库会话
- ``CurrentUserDep`` / ``CurrentOwnerDep``:从 ``Authorization: Bearer <token>`` 解出当前用户
  及其 owner_id。多用户后**不再有 OWNER_ID 常量**,凡是要用户作用域的地方都用 CurrentOwnerDep。
- ``/healthz`` 保持公开(前端健康探测不依赖登录)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_factory
from app.models.user import User
from app.repo.user import UserRepo
from app.service.auth.token import TokenError, decode_token

# auto_error=False:自己抛 401 并给中文 detail(默认实现抛 403 且文案不友好)
_bearer = HTTPBearer(auto_error=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Bearer token → 当前用户;缺失/无效/过期/用户已不存在一律 401。"""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("未登录")
    try:
        user_id, _username = decode_token(credentials.credentials)
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc
    user = await UserRepo(session).get_by_id(user_id)
    if user is None:
        raise _unauthorized("账号不存在")
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_current_owner(user: CurrentUserDep) -> int:
    """当前用户的 owner_id(业务表作用域都用它)。"""
    return user.id


CurrentOwnerDep = Annotated[int, Depends(get_current_owner)]
