#!/usr/bin/env python3
"""评测脚本共用工具：CSV 读写 + sha256 去重。

抽出来是因为 `candidates.py` 与 `runner.py` 各写过一份，重复实现迟早分叉。
只放**无状态**的纯工具函数，不放任何判定逻辑。
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

def load_existing(out_path: Path) -> dict[str, dict[str, str]]:
    """读已有的输出 CSV，按 rel_path 建索引（--resume 用）。"""
    if not out_path.exists():
        return {}
    with out_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return {row["rel_path"]: row for row in csv.DictReader(fh)}

def write_csv(out_path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """写 UTF-8-SIG + LF 的 CSV，列顺序固定为 columns。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def build_dupe_map(files: list[Path], root: Path) -> dict[str, str]:
    """sha256 相同的文件：按 rel_path 排序后第一个为 canonical，其余指向它。"""
    by_hash: dict[str, list[str]] = {}
    for path in files:
        by_hash.setdefault(sha256_of(path), []).append(path.relative_to(root).as_posix())
    dupe_of: dict[str, str] = {}
    for paths in by_hash.values():
        paths.sort()
        for other in paths[1:]:
            dupe_of[other] = paths[0]
    return dupe_of
