"""密码哈希与 JWT 单元测试(不碰数据库)。"""

from __future__ import annotations

import pytest

from app.service.auth.password import hash_password, verify_password
from app.service.auth.token import TokenError, create_access_token, decode_token


def test_hash_and_verify_roundtrip() -> None:
    stored = hash_password("my-secret-pw")
    assert stored.startswith("scrypt$")
    assert verify_password("my-secret-pw", stored) is True


def test_wrong_password_rejected() -> None:
    stored = hash_password("my-secret-pw")
    assert verify_password("other-pw", stored) is False


def test_same_password_hashes_differently() -> None:
    """盐随机:同一密码两次哈希结果不同,但都能验证通过。"""
    a = hash_password("same-pw")
    b = hash_password("same-pw")
    assert a != b
    assert verify_password("same-pw", a) is True
    assert verify_password("same-pw", b) is True


def test_malformed_hash_returns_false() -> None:
    for bad in ["", "plain-text", "scrypt$1$2", "md5$a$b$c$d$e"]:
        assert verify_password("whatever", bad) is False


def test_token_roundtrip() -> None:
    token = create_access_token(42, "alice")
    user_id, username = decode_token(token)
    assert user_id == 42
    assert username == "alice"


def test_decode_garbage_raises() -> None:
    with pytest.raises(TokenError):
        decode_token("not-a-jwt")
