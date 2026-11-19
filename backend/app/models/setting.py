"""app_settings —— 每个用户一行的运行时可改配置(设置页 + 章程)。

多用户后按 owner_id 唯一；charter 也在这里，所以每个账号有自己的一份章程。
字段为 NULL 时:非密钥项(base_url/model)回退 .env 默认值,API Key 不回退(必须自己配)。
响应里永不回显 key 全文。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, UniqueConstraint, func, text
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
    # B 站登录态（扫码登录半自动获取，2026-11-19）：
    #   bilibili_sessdata —— Playwright 扫码登录抓的 SESSDATA，给浏览器兜底过风控挑战用；
    #     NULL = 未配置，回退 .env 的 BILIBILI_SESSDATA（从没人配过，纯兼容）
    #   bilibili_sessdata_at —— 上次扫码时间（UI 显示 + 排查过期）
    #   bilibili_uid —— 登录用户 DedeUserID（UI 显示已登录谁）
    # ⚠️ 只给 Playwright 浏览器上下文用（HTTP 重放对 B 站风控无效，TLS 指纹打分，实测过）。
    bilibili_sessdata: Mapped[str | None] = mapped_column(Text)
    bilibili_sessdata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bilibili_uid: Mapped[str | None] = mapped_column(Text)
    # 知识库章程(问卷生成的自然语言,DB 优先于内置默认;NULL = 回退内置默认章程)
    charter: Mapped[str | None] = mapped_column(Text)
    # LLM 预算(per-user 自适应;NULL = 走兜底):
    #   llm_context_tokens —— 输入上下文上限;NULL = 不截断,整篇喂模型
    #   llm_max_output_tokens —— 输出上限;NULL = 用 organizer 兜底(ORGANIZER_DEFAULT_OUTPUT_TOKENS)
    # 不预设模型表:用户在设置页填自己模型/厂商的上限即可覆盖。
    llm_context_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    # 结构图开关(主人 2026-11-19 拍板:全局默认开,但必须「有依据才生」)
    #   NULL / true = 允许生成(仍由 diagram_basis 的结构依据闸把关)
    #   false       = 一律不生成
    # 「该不该画」不在这里决定——是代码算出来的(并列清单/流程阶段),不靠 LLM 自觉。
    allow_diagrams: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
