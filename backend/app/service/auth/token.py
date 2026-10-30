"""JWT 签发与校验。

payload 只放 user id / username / 过期时间,不放任何密钥。
密钥与有效期来自 config(DB 化没必要:改了密钥本来就该让所有 token 失效)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


class TokenError(Exception):
    """token 缺失/无效/过期。API 层统一翻成 401。"""


def create_access_token(user_id: int, username: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_expire_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> tuple[int, str]:
    """token → (user_id, username);无效/过期抛 TokenError。"""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        username = str(payload["username"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise TokenError("token 无效或已过期") from exc
    return user_id, username
