#!/usr/bin/env python3
"""baleen / eval · 阶段 2：批量跑器。

对 `eval/samples/` 逐个调用预测器，输出 `preds.csv`（10 列）：
    rel_path, pred_verdict, pred_quality, pred_note_type, pred_score,
    pred_action, cost_tokens, latency_ms, error, raw_json

`pred_action` 是 TASK.md §D5 三个指标（误拒率 / 误丢率 / 话题召回率）依赖的列：
    index / extract_spans / topic_dialog / drop

约束：
- 不 import backend 内部模块；`--predictor http` 只通过 HTTP 调产品 API。
- 单文件失败写进 `error` 列继续跑，不中断整体。
- `--predictor http` 未配置时**报错退出**，不假装成功。

用法：
    python eval/scripts/runner.py --samples eval/samples --out eval/preds.csv \
        --predictor fake --dry-run
    python eval/scripts/runner.py --samples eval/samples --out eval/preds.csv \
        --predictor fake --split test --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import build_dupe_map, load_existing, write_csv  # noqa: E402
from candidates import extract_features, list_samples  # noqa: E402
from predictor_fake import predict as fake_predict  # noqa: E402

COLUMNS = [
    "rel_path", "pred_verdict", "pred_quality", "pred_note_type", "pred_score",
    "pred_action", "cost_tokens", "latency_ms", "error", "raw_json",
]

ACTIONS = ("index", "extract_spans", "topic_dialog", "drop")

def load_splits(labels_path: Path) -> dict[str, str]:
    with labels_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return {row["rel_path"]: row.get("split", "") for row in csv.DictReader(fh)}

def http_predict(api_url: str, path: Path, timeout: float) -> dict[str, Any]:
    """把文件 POST 给产品 API，期望返回含 pred_* 字段的 JSON。"""
    boundary = "----baleenEvalBoundary"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="upload"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + path.read_bytes() + tail

    request = urllib.request.Request(
        api_url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or "pred_verdict" not in payload:
        raise ValueError(f"API 返回格式不符: {str(payload)[:200]}")
    return {
        "pred_verdict": payload.get("pred_verdict", ""),
        "pred_quality": payload.get("pred_quality", ""),
        "pred_note_type": payload.get("pred_note_type", ""),
        "pred_score": payload.get("pred_score", ""),
        "pred_action": payload.get("pred_action", ""),
        "error": payload.get("error", ""),
        "cost_tokens": payload.get("cost_tokens", 0),
        "raw": payload.get("raw_json", payload),
    }

def run_one(path: Path, root: Path, predictor: str, api_url: str, timeout: float,
            dupe_of: str) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    started = time.perf_counter()
    try:
        features = extract_features(path)
        if predictor == "fake":
            result = fake_predict(path, features, dupe_of)
            result.setdefault("cost_tokens", 0)
        else:
            result = http_predict(api_url, path, timeout)
    except (urllib.error.URLError, OSError) as exc:
        result = {"error": f"{type(exc).__name__}: {exc}", "raw": {"error": str(exc)}}
    except Exception as exc:  # noqa: BLE001 单文件失败不许中断整体
        result = {"error": f"{type(exc).__name__}: {exc}", "raw": {"error": str(exc)}}

    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "rel_path": rel,
        "pred_verdict": result.get("pred_verdict", ""),
        "pred_quality": result.get("pred_quality", ""),
        "pred_note_type": result.get("pred_note_type", ""),
        "pred_score": result.get("pred_score", ""),
        "pred_action": result.get("pred_action", ""),
        "cost_tokens": result.get("cost_tokens", 0),
        "latency_ms": latency_ms,
        "error": result.get("error", ""),
        "raw_json": json.dumps(result.get("raw", {}), ensure_ascii=False),
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量跑预测器，输出 preds.csv")
    parser.add_argument("--samples", default="eval/samples", help="样本根目录")
    parser.add_argument("--out", default="eval/preds.csv", help="输出 CSV 路径")
    parser.add_argument("--predictor", choices=("fake", "http"), default="fake")
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--labels", default="eval/labels.csv",
                        help="只用于 --split 过滤，绝不写回")
    parser.add_argument("--api-url", default="", help="--predictor http 时必须提供")
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP 超时秒数")
    parser.add_argument("--resume", action="store_true", help="跳过输出里已有的 rel_path")
    parser.add_argument("--dry-run", action="store_true", help="只校验参数并打印计划，不写文件")
    args = parser.parse_args(argv)

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[error] 样本目录不存在: {root}", file=sys.stderr)
        return 2
    files = list_samples(root)
    if not files:
        print(f"[error] {root} 下没有文件", file=sys.stderr)
        return 2

    if args.predictor == "http" and not args.api_url:
        print("[error] --predictor http 需要 --api-url（产品 API 未就绪时请改用 fake）",
              file=sys.stderr)
        return 2

    if args.split != "all":
        labels_path = Path(args.labels)
        if not labels_path.exists():
            print(f"[error] --split {args.split} 需要 {labels_path} 提供划分", file=sys.stderr)
            return 2
        splits = load_splits(labels_path)
        files = [
            p for p in files
            if splits.get(p.relative_to(root).as_posix()) == args.split
        ]
        if not files:
            print(f"[error] {args.split} 划分为空（labels 与样本不一致？）", file=sys.stderr)
            return 2

    if args.dry_run:
        print(f"[dry-run] predictor={args.predictor} split={args.split} "
              f"files={len(files)} out={args.out}（未写任何文件）")
        return 0

    existing = load_existing(Path(args.out)) if args.resume else {}
    dupe_map = build_dupe_map(list_samples(root), root)

    rows: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in existing:
            rows.append({key: existing[rel].get(key, "") for key in COLUMNS})
            continue
        rows.append(run_one(path, root, args.predictor, args.api_url,
                            args.timeout, dupe_map.get(rel, "")))

    write_csv(Path(args.out), COLUMNS, rows)

    errors = sum(1 for row in rows if str(row.get("error", "")).strip())
    counts = {action: 0 for action in ACTIONS}
    for row in rows:
        action = str(row.get("pred_action", ""))
        if action in counts:
            counts[action] += 1
    print(f"[ok] {len(rows)} 行 → {args.out}；error={errors}；"
          + " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
