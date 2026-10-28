"""config 的 .env 加载路径测试(线 F)。

绝对不碰真的 baleen/.env(AGENTS §3 硬约束 3):用 tmp_path 造临时 .env + monkeypatch。
"""

from pydantic_settings import SettingsConfigDict

from app.core import config as cfg


def test_root_dir_absolute_and_has_env_example():
    """ROOT_DIR 解析为绝对路径,且指向 baleen/(那里有 .env.example)。"""
    assert cfg.ROOT_DIR.is_absolute()
    assert (cfg.ROOT_DIR / ".env.example").exists(), ".env.example 应在 baleen/(ROOT_DIR)"


def test_reads_env_file_from_root_dir(monkeypatch, tmp_path):
    """把 ROOT_DIR 指向临时目录、写一个 .env,断言 Settings 能读到里面的值。"""
    fake_env = tmp_path / ".env"
    fake_env.write_text("EMBEDDING_MODEL_PATH=/fake/model/path-for-test\n", encoding="utf-8")
    # 环境变量优先级高于 .env 文件,清掉避免 embed conftest 注入的真值干扰
    monkeypatch.delenv("EMBEDDING_MODEL_PATH", raising=False)
    # env_file 在类定义时就绑定了原 ROOT_DIR,所以同时把 model_config 指向临时 .env
    monkeypatch.setattr(cfg, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        cfg.Settings,
        "model_config",
        SettingsConfigDict(env_file=(fake_env,), env_file_encoding="utf-8", extra="ignore"),
    )
    cfg.get_settings.cache_clear()
    try:
        s = cfg.get_settings()
        assert s.embedding_model_path == "/fake/model/path-for-test"
    finally:
        cfg.get_settings.cache_clear()


def test_resolve_charter_prefers_charter_md(monkeypatch, tmp_path):
    """charter.md 存在时优先读文件全文。"""
    (tmp_path / "charter.md").write_text("# 我的章程\n只收自己想明白的东西", encoding="utf-8")
    monkeypatch.setattr(cfg, "ROOT_DIR", tmp_path)
    assert cfg.get_settings().resolve_charter() == "# 我的章程\n只收自己想明白的东西"


def test_resolve_charter_falls_back_to_builtin(monkeypatch, tmp_path):
    """没有 charter.md → 用内置默认字符串。"""
    monkeypatch.setattr(cfg, "ROOT_DIR", tmp_path)
    settings = cfg.get_settings()
    assert settings.resolve_charter() == settings.charter
    assert settings.resolve_charter()  # 非空
