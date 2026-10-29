"""ORM 基类。

pgvector 的 halfvec 类型用官方 `pgvector.sqlalchemy.HALFVEC`(见 embedding.py),
不再用自定义 UserDefinedType——线 E 已批准 uv add pgvector。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有模型的公共基类。"""
