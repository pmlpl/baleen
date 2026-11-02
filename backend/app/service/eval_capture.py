"""评测候选样本自动归档(「使用即积累」,2026-11-02 主人拍板的 C 路线)。

每次**真实导入**完成后,把原始文件复制进 ``eval/candidates/``、追加登记行到
``eval/candidates_log.csv``——主人日常用 baleen 的同时,评测语料池自动变大;
主人有空时复核,挑中的挪进 ``eval/samples/_inbox/`` 走既有流程
(``candidates.py --resume`` 重扫特征 → ``--labels-out`` 出标注骨架)。

设计口径:
- **best-effort**:归档失败只记日志,绝不影响导入结果(与阶段3摘要同款纪律)
- **去重**:按 sha256,同内容只归档一次(重复导入/去重命中不重复归档)
- **隐私**:candidates/ 与 candidates_log.csv 均 .gitignore——复核选中才挪进
  samples 并提交;登记表里的元信息(判定/质量)也留在本地
- **测试不产生候选**:settings.eval_capture_enabled 默认 True,api conftest 显式关
"""

from __future__ import annotations

import csv
import logging
import shutil
from datetime import datetime
from pathlib import Path

from app.core.config import ROOT_DIR, get_settings

log = logging.getLogger(__name__)

# 归档区(仓库内但不进 git;文件是主人笔记的副本,复核选中才转正)
CANDIDATES_DIR = ROOT_DIR / "eval" / "candidates"
LOG_PATH = ROOT_DIR / "eval" / "candidates_log.csv"

LOG_COLUMNS = [
    "captured_at",
    "sha256",
    "archived_as",
    "filename",
    "ext",
    "size_bytes",
    "parse_status",
    "g1_verdict",
    "g2_verdict",
    "quality",
    "note_id",
    "owner_id",
    "via",
    "reviewed",
]


def capture_candidate(
    file_path: str | Path,
    *,
    sha256: str,
    parse_status: str,
    g1_verdict: str | None,
    g2_verdict: str | None,
    quality: str | None,
    note_id: int | None,
    owner_id: int,
    via: str,
) -> Path | None:
    """归档一个导入完成的原始文件;任何失败都吞掉并返回 None(best-effort)。

    返回归档副本路径(成功时);调用方不依赖返回值做任何事。
    """
    settings = get_settings()
    if not settings.eval_capture_enabled:
        return None
    src = Path(file_path)
    try:
        if not src.is_file():
            return None
        # 同内容只归档一次:目标名带 sha 前缀,存在即跳过(幂等)
        archived_name = f"{sha256[:12]}_{src.name}"
        target = CANDIDATES_DIR / archived_name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        _append_log(
            captured_at=datetime.now().isoformat(timespec="seconds"),
            sha256=sha256,
            archived_as=archived_name,
            filename=src.name,
            ext=src.suffix.lower().lstrip("."),
            size_bytes=src.stat().st_size,
            parse_status=parse_status,
            g1_verdict=g1_verdict or "",
            g2_verdict=g2_verdict or "",
            quality=quality or "",
            note_id=note_id if note_id is not None else "",
            owner_id=owner_id,
            via=via,
            reviewed="",  # 主人复核后手写 ok/reject
        )
        return target
    except Exception as exc:  # noqa: BLE001 —— 归档是锦上添花,吞掉一切
        log.warning("评测候选归档失败(不影响导入): %s", exc)
        return None


def _append_log(**row: object) -> None:
    """追加一行登记;文件不存在时先写表头。"""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


__all__ = ["CANDIDATES_DIR", "LOG_COLUMNS", "LOG_PATH", "capture_candidate"]
