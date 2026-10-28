"""应用配置:全部来自环境变量(可选 .env),不硬编码任何路径与密钥。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://baleen:baleen@localhost:5432/baleen"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    # bge-large-zh-v1.5 的本地 HF cache 路径(AGENTS.md §6:.env.example 有,代码不许硬编码绝对路径)
    embedding_model_path: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
