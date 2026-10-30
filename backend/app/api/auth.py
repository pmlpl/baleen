"""认证 API:注册 / 登录 / 当前用户。

- 开放注册(单库多人用,各自数据隔离)
- 密码只存 scrypt 哈希;登录失败统一 401「用户名或密码错误」,不泄露用户是否存在
- 第一个注册的账号拿到 id=1,继承项目单用户时期留下的笔记/会话/设置
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentUserDep, SessionDep
from app.repo.user import UserRepo
from app.service.auth.password import hash_password, verify_password
from app.service.auth.token import create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_MIN = 3
USERNAME_MAX = 32
PASSWORD_MIN = 8
PASSWORD_MAX = 128


class Credentials(BaseModel):
    username: str = Field(min_length=USERNAME_MIN, max_length=USERNAME_MAX)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < USERNAME_MIN:
            raise ValueError(f"用户名至少 {USERNAME_MIN} 个字符")
        return stripped


class UserOut(BaseModel):
    id: int
    username: str


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/register", response_model=AuthOut)
async def register(payload: Credentials, session: SessionDep) -> AuthOut:
    repo = UserRepo(session)
    if await repo.get_by_username(payload.username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用")
    user = await repo.create(
        username=payload.username, password_hash=hash_password(payload.password)
    )
    await session.commit()
    return AuthOut(
        access_token=create_access_token(user.id, user.username),
        user=UserOut(id=user.id, username=user.username),
    )


@router.post("/login", response_model=AuthOut)
async def login(payload: Credentials, session: SessionDep) -> AuthOut:
    user = await UserRepo(session).get_by_username(payload.username)
    # 用户不存在与密码错误给同一句:不暴露某个用户名是否注册过
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthOut(
        access_token=create_access_token(user.id, user.username),
        user=UserOut(id=user.id, username=user.username),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUserDep) -> UserOut:
    return UserOut(id=user.id, username=user.username)
