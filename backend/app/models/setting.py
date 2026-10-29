"""app_settings —— 运行时可改的模型配置(设置页),单行 id=1。

DB 值优先于 .env 缺省;字段为 NULL 时回退 .env 对应项。api_key 明文存库
(与 .env 同级别;单用户本地应用,可接受,响应里永不回显全文)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    llm_base_url: Mapped[str | None] = mapped_column(Text)
    llm_api_key: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
