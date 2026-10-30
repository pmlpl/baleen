"""ORM 模型聚合入口。

字段与类型严格照 ADR 0003 §2,未自行增删列。relationship 不增加数据库列,
仅为后续 repo 层使用方便。Base.metadata 是 Alembic autogenerate 的目标。
"""

from .base import Base
from .chunk import Chunk
from .embedding import Embedding
from .job import Job
from .note import Note
from .qa import QaMessage, QaSession
from .review_run import ReviewRun
from .setting import AppSettings
from .source import Source
from .user import User

__all__ = [
    "AppSettings",
    "Base",
    "Chunk",
    "Embedding",
    "Job",
    "Note",
    "QaMessage",
    "QaSession",
    "ReviewRun",
    "Source",
    "User",
]
