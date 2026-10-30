#!/usr/bin/env python
"""G2 真跑评测:产品真链路 parse→G1→G2,产出 preds.csv 喂 metrics.py 出十节报告。

    cd backend && uv run python scripts/g2_eval.py --samples ../eval/samples \
        --out ../eval/preds_live.csv --limit 2            # 试跑
    cd backend && LLM_BASE_URL=https://api.deepseek.com/v1 \
        LLM_API_KEY=sk-xxx LLM_MODEL=deepseek-flash \
        uv run python scripts/g2_eval.py --samples ../eval/samples --out ../eval/preds_live.csv

- 与 g2_smoke.py 的分工:g2_smoke 单独考 G2(直接喂 parse 结果);本脚本跑**产品真链路** ——
  G0 非 ok 确定性拒收(产品本就不进闸)、G1 启发式拒收、都过了才烧 G2 的 LLM。
  评测对象是整条导入决策,不是孤立的 G2。
- preds 列与 eval/scripts/runner.py 的 COLUMNS 逐列一致(rel_path 同为相对 samples 根的
  posix 路径,如 ``_inbox/xxx.md``),metrics.py 直接可用。G1 拒收不给 1-5 分
  (G1 的 score 是 0-1 概率,与 G2 的 1-5 不同量纲,留空并在报告注明)。
- LLM 三件套走 pydantic-settings:进程环境变量 > .env,换模型零文件改动。
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.service.gate import judge as judge_g1  # noqa: E402
from app.service.gate.g2 import judge as judge_g2  # noqa: E402
from app.service.llm import LLMClient, LLMError  # noqa: E402
from app.service.parse import parse_file  # noqa: E402

COLUMNS = [
    "rel_path", "pred_verdict", "pred_quality", "pred_note_type", "pred_score",
    "pred_action", "cost_tokens", "latency_ms", "error", "raw_json",
]


def _row(
    rel_path: str,
    verdict: str,
    quality: str,
    note_type: str,
    score: str,
    action: str,
    cost: int,
    latency: int,
    error: str,
    raw: dict,
) -> dict[str, str]:
    return {
        "rel_path": rel_path,
        "pred_verdict": verdict,
        "pred_quality": quality,
        "pred_note_type": note_type,
        "pred_score": score,
        "pred_action": action,
        "cost_tokens": str(cost),
        "latency_ms": str(latency),
        "error": error,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def predict_one(
    path: Path, rel_path: str, charter: str, client: LLMClient, json_mode: bool
) -> dict:
    """一个样本走完整条产品判定链,返回 preds.csv 的一行。"""
    t0 = time.perf_counter()
    pr = parse_file(path)
    if pr.parse_status.value != "ok":
        # 产品真行为:非 ok 不进任何闸、不产笔记 → 确定性拒收
        return _row(
            rel_path, "reject", "", "", "", "drop", 0,
            int(round((time.perf_counter() - t0) * 1000)), "",
            {"stage": "G0", "parse_status": pr.parse_status.value,
             "message": pr.message or "", "warnings": pr.warnings},
        )

    g1 = judge_g1(pr)
    if g1.verdict == "reject":
        return _row(
            rel_path, "reject", g1.quality, "", "", g1.action, 0,
            int(round((time.perf_counter() - t0) * 1000)), "",
            {"stage": "G1", "reasons": g1.reasons, "meta": g1.meta},
        )

    g2 = judge_g2(
        pr,
        charter,
        filename=path.name,
        ext=path.suffix.lower(),
        size_bytes=path.stat().st_size,
        client=client,
        json_mode=json_mode,
    )
    return _row(
        rel_path, g2.verdict, g2.quality, g2.note_type, str(g2.score), g2.action,
        g2.cost_tokens or 0, int(round(g2.latency_ms)), "",
        {"stage": "G2", "reasons": g2.reasons, "missing": g2.missing,
         "model": g2.model, "degraded": bool((g2.meta or {}).get("degraded"))},
    )


def _load_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    with open(out_path, encoding="utf-8-sig", newline="") as fh:
        return {
            row["rel_path"]
            for row in csv.DictReader(fh)
            if row.get("pred_verdict", "").strip()
        }


def _write_csv(out_path: Path, rows: list[dict]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="G2 真跑评测:parse→G1→G2 真链路 → preds.csv")
    ap.add_argument("--samples", default="../eval/samples", help="样本根目录(rel_path 相对它)")
    ap.add_argument("--out", default="../eval/preds_live.csv", help="preds 输出 CSV")
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理 N 个文件")
    ap.add_argument("--delay", type=float, default=2.0, help="文件之间 sleep 秒数(默认 2)")
    ap.add_argument("--resume", action="store_true", help="跳过 out CSV 里已有判定的 rel_path")
    args = ap.parse_args()

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[skip] 样本目录不存在:{root}")
        return 0
    files = [
        p for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    ]
    rel_of = {p: p.relative_to(root).as_posix() for p in files}
    files.sort(key=lambda p: rel_of[p].lower())
    print(f"[eval] 样本 {len(files)} 个;charter {len(get_settings().resolve_charter())} 字符")

    try:
        client = LLMClient.from_settings(get_settings())
        caps = client.probe()
    except LLMError as exc:
        print(f"[skip] LLM client/probe 失败:{type(exc).__name__}: {exc}")
        return 0
    print(
        f"probe: model={caps.model} chat={caps.chat} json_mode={caps.json_mode} {caps.notes}"
    )
    charter = get_settings().resolve_charter()

    done = _load_done(Path(args.out)) if args.resume else set()
    if args.resume:
        print(f"[resume] 已有判定 {len(done)} 条:{args.out}")
    rows: list[dict] = []
    if Path(args.out).exists():
        with open(Path(args.out), encoding="utf-8-sig", newline="") as fh:
            rows = [row for row in csv.DictReader(fh)]

    processed = 0
    stage_counter: dict[str, int] = {}
    total_cost = 0
    for path in files:
        rel = rel_of[path]
        if rel in done:
            continue
        if args.limit is not None and processed >= args.limit:
            break
        if processed and args.delay > 0:
            time.sleep(args.delay)
        try:
            row = predict_one(path, rel, charter, client, caps.json_mode)
        except LLMError as exc:
            row = _row(rel, "", "", "", "", "", 0, 0,
                       f"{type(exc).__name__}: {exc}", {"stage": "error"})
        except Exception as exc:  # noqa: BLE001 —— 单文件失败不中断整批
            row = _row(rel, "", "", "", "", "", 0, 0,
                       f"{type(exc).__name__}: {exc}", {"stage": "error"})
        rows.append(row)
        _write_csv(Path(args.out), rows)  # 逐行落盘,中断不丢进度
        total_cost += int(row["cost_tokens"])
        stage = (json.loads(row["raw_json"]).get("stage") or "?") if row["raw_json"] else "?"
        stage_counter[stage] = stage_counter.get(stage, 0) + 1
        processed += 1
        print(
            f"{stage:<3} {rel[:44]:<46} verdict={row['pred_verdict'] or 'ERR':<8} "
            f"action={row['pred_action'] or '-':<13} cost={row['cost_tokens']:<6} "
            f"{row['latency_ms']}ms"
            + (f" error={row['error']}" if row["error"] else "")
        )

    print(
        f"\n本次处理 {processed} 个;阶段分布 {stage_counter};"
        f"总花费 {total_cost} token;preds → {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
