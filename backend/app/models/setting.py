"""app_settings —— 每个用户一行的运行时可改配置(设置页 + 章程)。

多用户后按 owner_id 唯一；charter 也在这里，所以每个账号有自己的一份章程。
字段为 NULL 时:非密钥项(base_url/model)回退 .env 默认值,API Key 不回退(必须自己配)。
响应里永不回显 key 全文。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSettings(Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("owner_id", name="app_settings_owner_uq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    llm_base_url: Mapped[str | None] = mapped_column(Text)
    llm_api_key: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(Text)
    # 工具链:库外问题的联网搜索(Tavily 免费档,1000 次/月)
    tavily_api_key: Mapped[str | None] = mapped_column(Text)
    # 抖音 cookie 字符串——已废弃：抖音改走 Playwright 免 cookie 提取（douyin_extractor.py）。
    # 列保留不删（避免迁移），此字段不再读写。
    douyin_cookies: Mapped[str | None] = mapped_column(Text)
    # 知识库章程(问卷生成的自然语言,DB 优先于内置默认;NULL = 回退内置默认章程)
    charter: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
