"""应用配置:全部来自环境变量(可选 .env),不硬编码任何路径与密钥。"""

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 绝对路径定位:无论从哪个 CWD 启动,都能找到仓库根的 .env(与 docker compose 共用一份)
BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
ROOT_DIR = BASE_DIR.parent  # baleen/  (.env / charter.md 在这里)

log = logging.getLogger(__name__)

# 内置默认 charter(没有 baleen/charter.md 时兜底;CHARTER 环境变量可覆盖)
DEFAULT_CHARTER = (
    "我是一名软件过程技术方向的本科生,这个笔记库只收「以后还用得上」的内容。\n"
    "收:需求与设计文档、测试计划与缺陷记录(含现象/根因/修复)、有实际进展的项目过程记录、"
    "自写的技术笔记与论文整理、可复现的问题记录(环境配置踩坑、性能排查、bug 复盘)。\n"
    "不收:无上下文的报错堆栈、纯模板周报、聊天记录、下载的发票与产品说明书、纯截图、"
    "与个人知识库无关的外部资料。\n"
    "判据:能留下一句「为什么这么做」的是知识,收;只在汇报「我做了什么」的不是知识,不收。"
)


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
    # 首答提速:API 进程启动后后台线程预加载嵌入模型(消除第一问的冷启动等待)
    embed_warmup_on_startup: bool = True
    # 评测候选自动归档(C 路线「使用即积累」):真实导入完成后把原文件副本归档进
    # eval/candidates/(gitignore)并登记,主人复核后转正进 samples;测试 conftest 显式关
    eval_capture_enabled: bool = True

    # ---------- LLM(AGENTS.md 决策 9:OpenAI 兼容协议,用户自带 key)----------
    # v1 单库:charter 就是一个自然语言字符串,不建 knowledge_bases 表(HANDOFF §2 已拍板)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""  # 真实 key 只进 .env,永不入代码/日志
    llm_model: str = "gpt-4o-mini"
    charter: str = DEFAULT_CHARTER

    # ---------- 认证(JWT)----------
    # ⚠️ 默认值仅供本地开发;部署前必须在 .env 里换成随机长串(改了会让已签发的 token 全部失效)
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 天

    # ---------- LLM 输出预算 ----------
    # 推理模型把 reasoning 也计入 max_tokens,给小了正文会整条为空。
    # 默认按主流长上下文模型的上限设(8192),可用 LLM_MAX_OUTPUT_TOKENS 覆盖。
    llm_max_output_tokens: int = 8192
    # 单次结构化任务的超时(秒):整理整篇比单轮问答慢得多
    llm_long_task_timeout: float = 300.0

    def resolve_charter(self) -> str:
        """charter 解析优先级:``baleen/charter.md`` 全文 > `CHARTER` 环境变量 > 内置默认。

        文件存在但读取失败(权限/编码)时记 warning 并回退,绝不抛异常。
        """
        path = ROOT_DIR / "charter.md"
        if not path.exists():
            return self.charter
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("读取 %s 失败(%s),回退到内置 charter", path, exc)
            return self.charter
        return text or self.charter

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
