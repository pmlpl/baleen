"""「使用即积累」评测候选归档测试(C 路线)。

- 单元:capture_candidate 的成功/幂等/禁用/吞错;
- 集成:run_import_async 成功导入触发归档、dedupe 命中不重复登记。
"""

from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.service import eval_capture
from app.service.eval_capture import LOG_COLUMNS, capture_candidate

CONTENT = "# 候选样本\n\n这是一段足够长的真实导入内容,用于候选归档验证。\n".encode()


@pytest.fixture
def capture_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """候选区/登记表重定向到 tmp;并强制开启(全测试进程默认关)。"""
    cdir = tmp_path / "candidates"
    logp = tmp_path / "candidates_log.csv"
    monkeypatch.setattr(eval_capture, "CANDIDATES_DIR", cdir)
    monkeypatch.setattr(eval_capture, "LOG_PATH", logp)
    monkeypatch.setattr(
        eval_capture, "get_settings", lambda: SimpleNamespace(eval_capture_enabled=True)
    )
    return SimpleNamespace(dir=cdir, log=logp)


def _capture(path: Path, sha: str = "abc123def456") -> Path | None:
    return capture_candidate(
        path,
        sha256=sha,
        parse_status="ok",
        g1_verdict="accept",
        g2_verdict="accept",
        quality="core",
        note_id=42,
        owner_id=1,
        via="import:arq-worker",
    )


def _log_rows(logp: Path) -> list[dict[str, str]]:
    with logp.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_capture_copies_file_and_appends_log(tmp_path: Path, capture_env: SimpleNamespace) -> None:
    src = tmp_path / "note.md"
    src.write_bytes(CONTENT)
    result = _capture(src)
    assert result is not None and result.is_file()
    assert result.read_bytes() == CONTENT
    assert result.name.startswith("abc123def456_"), "归档名带 sha 前缀"
    rows = _log_rows(capture_env.log)
    assert len(rows) == 1
    row = rows[0]
    assert list(row.keys()) == LOG_COLUMNS
    assert row["parse_status"] == "ok" and row["g1_verdict"] == "accept"
    assert row["note_id"] == "42" and row["via"] == "import:arq-worker"
    assert row["reviewed"] == "", "复核列留空等人"


def test_capture_same_sha_reuses_file_but_logs_event(
    tmp_path: Path, capture_env: SimpleNamespace
) -> None:
    """文件按内容去重(只存一份);log 记事件流水(重试成功=两次真实导入)。"""
    src = tmp_path / "note.md"
    src.write_bytes(CONTENT)
    _capture(src)
    _capture(src)
    files = list(capture_env.dir.iterdir())
    assert len(files) == 1, "同 sha 文件只归档一份"
    assert len(_log_rows(capture_env.log)) == 2


def test_capture_disabled_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_capture, "CANDIDATES_DIR", tmp_path / "c")
    monkeypatch.setattr(eval_capture, "LOG_PATH", tmp_path / "log.csv")
    monkeypatch.setattr(
        eval_capture, "get_settings", lambda: SimpleNamespace(eval_capture_enabled=False)
    )
    src = tmp_path / "note.md"
    src.write_bytes(CONTENT)
    assert _capture(src) is None
    assert not (tmp_path / "c").exists()


def test_capture_swallows_missing_file(capture_env: SimpleNamespace) -> None:
    assert _capture(capture_env.dir / "ghost.md") is None


def test_capture_swallows_io_error(
    tmp_path: Path, capture_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复制目标不可写 → 返回 None 不抛(归档绝不影响导入)。"""
    capture_env.dir.write_bytes(b"x")  # 把目录路径变成文件,制造 OSError
    src = tmp_path / "note.md"
    src.write_bytes(CONTENT)
    assert _capture(src) is None
