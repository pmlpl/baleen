"""应用配置:全部来自环境变量(可选 .env),不硬编码任何路径与密钥。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 绝对路径定位:无论从哪个 CWD 启动,都能找到仓库根的 .env(与 docker compose 共用一份)
BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
ROOT_DIR = BASE_DIR.parent  # baleen/  (.env 在这里)


class Settings(BaseSettings):
    # 顺序刻意:根 .env 先读,backend/.env(若存在)覆盖它 —— 便于本地临时调试
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://baleen:baleen@localhost:5432/baleen"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    # bge-large-zh-v1.5 的本地 HF cache 路径(AGENTS.md §6:.env.example 有,代码不许硬编码绝对路径)
    embedding_model_path: str = ""

    # ---------- LLM(AGENTS.md 决策 9:OpenAI 兼容协议,用户自带 key)----------
    # v1 单库:charter 就是一个自然语言字符串,不建 knowledge_bases 表(HANDOFF §2 已拍板)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""  # 真实 key 只进 .env,永不入代码/日志
    llm_model: str = "gpt-4o-mini"
    charter: str = (
        "我是一名软件过程技术方向的本科生,这个笔记库只收「以后还用得上」的内容。\n"
        "收:需求与设计文档、测试计划与缺陷记录(含现象/根因/修复)、有实际进展的项目过程记录、"
        "自写的技术笔记与论文整理、可复现的问题记录(环境配置踩坑、性能排查、bug 复盘)。\n"
        "不收:无上下文的报错堆栈、纯模板周报、聊天记录、下载的发票与产品说明书、纯截图、"
        "与个人知识库无关的外部资料。\n"
        "判据:能留下一句「为什么这么做」的是知识,收;只在汇报「我做了什么」的不是知识,不收。"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
