"""密码哈希:标准库 scrypt(内存硬 KDF),不引第三方依赖。

存储格式:``scrypt$n$r$p$<salt_b64>$<hash_b64>``
把参数写进哈希串,以后调参也能验证旧密码(逐条按自己的参数算)。
"""

from __future__ import annotations

import base64
import hashlib
import secrets

# 参数取 log2(N)=14 → N=16384;内存约 16 MB、耗时约 50-100ms(本地单机够用)
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_LEN = 32


def hash_password(password: str) -> str:
    """明文 → 可存库的哈希串(每次调用盐都不同)。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """明文 + 存库哈希串 → 是否匹配。

    哈希串格式不认识/被截断 → 返回 False(不抛,让调用方统一按「密码错误」处理)。
    """
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)
