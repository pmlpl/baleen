#!/usr/bin/env python
"""G2 归属闸冒烟:默认 dry-run,只检查 prompt 组装;**一次 LLM 都不调**。

    cd backend && uv run python scripts/g2_smoke.py --samples ../eval/samples/_inbox
    cd backend && uv run python scripts/g2_smoke.py --samples ../eval/samples/_inbox --live
    cd backend && uv run python scripts/g2_smoke.py --samples ../eval/samples/_inbox \
        --live --delay 3 --limit 5
    cd backend && uv run python scripts/g2_smoke.py --samples ../eval/samples/_inbox --live --resume

- 默认(dry-run):打印每个 ok 文件的 prompt token 估算/截断情况 + 预算分配 + 关键政策片段,
  确认无一超 4000 token。
- ``--live``:真调 LLM,打印每条花费与总花费,并与 ``eval/labels.csv`` 做只读对照。
  - ``--delay N``:每个文件之间 sleep N 秒(默认 3),避免触发网关限流。
  - ``--limit N``:本次只处理 N 个文件(便于分批打)。
  - ``--resume``:跳过已出结果的 rel_path(结果追加在 ``backend/tmp/g2_live_results.jsonl``)。
- labels.csv **只读,一个字节都不改**;缺目录/缺 key 都优雅跳过(exit 0)。
"""

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.service.gate.g2 import build_messages, judge  # noqa: E402
from app.service.llm import LLMClient, LLMError  # noqa: E402
from app.service.parse import ParseResult, parse_file  # noqa: E402

BUDGET = 4000
RESULTS_PATH = Path(__file__).resolve().parents[1] / "tmp" / "g2_live_results.jsonl"
# rubric §7 的一致性映射:出现这些组合说明 G2 输出自相矛盾
INVALID_COMBOS = {
    ("accept", "extract_spans"),
    ("accept", "topic_dialog"),
    ("accept", "drop"),
    ("partial", "index"),
    ("partial", "topic_dialog"),
    ("partial", "drop"),
    ("reject", "index"),
    ("reject", "extract_spans"),
}


def _find_labels(samples_root: Path, override: str | None) -> Path | None:
    if override:
        return Path(override)
    for p in (samples_root.parent, samples_root.parent.parent):
        cand = p / "labels.csv"
        if cand.exists():
            return cand
    return None


def _read_labels(path: Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        return {row["filename"]: row for row in csv.DictReader(fh)}


def _ok_files(root: Path) -> list[tuple[Path, ParseResult]]:
    items = []
    for f in sorted(root.iterdir()):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        pr = parse_file(f)
        if pr.parse_status.value == "ok":
            items.append((f, pr))
    return items


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    idx = min(len(values) - 1, int(round((len(values) - 1) * q)))
    return sorted(values)[idx]


def _load_done(path: Path) -> set[str]:
    """--resume 用:已出结果的 rel_path 集合(坏行跳过)。"""
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            done.add(json.loads(line)["rel_path"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return done


def _append_result(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _dry_run(files, charter: str) -> None:
    tokens: list[int] = []
    sample_tokens: list[int] = []
    truncated = 0
    charter_tokens = 0
    warnings: list[str] = []
    first_system = ""
    for f, pr in files:
        bundle = build_messages(
            pr,
            charter,
            filename=f.name,
            ext=f.suffix.lower(),
            size_bytes=f.stat().st_size,
        )
        first_system = first_system or bundle.messages[0]["content"]
        tokens.append(bundle.estimated_tokens)
        sample_tokens.append(bundle.sample_tokens)
        truncated += bundle.truncated
        charter_tokens = bundle.charter_tokens
        warnings = bundle.warnings
        mark = "yes" if bundle.truncated else "no"
        print(
            f"ok   {f.name[:40]:<42} est={bundle.estimated_tokens:<5} "
            f"charter={bundle.charter_tokens:<5} sample={bundle.sample_tokens:<5} "
            f"chars={bundle.original_chars:<6}→kept={bundle.kept_chars:<5} truncated={mark}"
        )
    over = [t for t in tokens if t > BUDGET]
    print(
        f"\n预算分配: 总预算={BUDGET} | charter={charter_tokens} token | "
        f"正文采样中位数={_pct(sample_tokens, 0.5)} token"
    )
    print(
        f"prompt token 估算(共 {len(tokens)} 个 ok 文件): "
        f"min={min(tokens, default=0)} median={_pct(tokens, 0.5)} "
        f"p90={_pct(tokens, 0.9)} max={max(tokens, default=0)}"
    )
    print(f"超 {BUDGET} token 的文件: {len(over)} 个;发生截断: {truncated} 个")
    for w in warnings:
        print(f"[warn] {w}")
    print("\n关键政策检查(供 grep):")
    print(f"  不走这一问={('不走这一问' in first_system)}")
    print(f"  拿不准就 accept={('拿不准就 accept' in first_system)}")
    print("--- system prompt(第 1 个文件) ---")
    print(first_system)


def _live(
    files,
    charter: str,
    labels: dict[str, dict],
    samples_root: Path,
    *,
    delay: float,
    limit: int | None,
    resume: bool,
    results_path: Path,
) -> None:
    try:
        client = LLMClient.from_settings(get_settings())
    except LLMError as exc:
        print(
            f"[skip] 无法构造 LLM client:{exc}\n       请在 .env 里配置 LLM_API_KEY 后再用 --live"
        )
        return
    try:
        caps = client.probe()
    except LLMError as exc:
        print(f"[skip] probe 失败:{type(exc).__name__}: {exc}")
        return
    print(
        f"probe: model={caps.model} models_endpoint={caps.models_endpoint} "
        f"chat={caps.chat} json_mode={caps.json_mode} {caps.notes}"
    )
    done = _load_done(results_path) if resume else set()
    if resume:
        print(f"[resume] 已有 {len(done)} 条结果:{results_path}")
    print(f"[throttle] delay={delay}s limit={limit or '全部'} 结果文件={results_path}")

    total = 0
    degraded = 0
    failures = 0
    processed = 0
    matrix: dict[str, Counter] = defaultdict(Counter)
    action_agree = 0
    action_total = 0
    invalid: Counter = Counter()

    for f, pr in files:
        if f.name in done:
            print(f"skip {f.name[:36]:<38} 已有结果(--resume)")
            continue
        if limit is not None and processed >= limit:
            break
        if processed and delay > 0:
            time.sleep(delay)
        try:
            r = judge(
                pr,
                charter,
                filename=f.name,
                ext=f.suffix.lower(),
                size_bytes=f.stat().st_size,
                client=client,
                json_mode=caps.json_mode,
            )
        except LLMError as exc:  # 单文件失败不中断整批,便于分批重跑
            failures += 1
            print(f"err  {f.name[:36]:<38} {type(exc).__name__}: {exc}")
            continue
        processed += 1
        total += r.cost_tokens or 0
        degraded += bool(r.meta.get("degraded"))
        print(
            f"ok   {f.name[:36]:<38} verdict={r.verdict:<8} quality={r.quality:<10} "
            f"action={r.action:<13} score={r.score} cost={r.cost_tokens or 0:<6} "
            f"{r.latency_ms:.0f}ms"
        )
        _append_result(
            results_path,
            {
                "rel_path": f.name,
                "verdict": r.verdict,
                "quality": r.quality,
                "note_type": r.note_type,
                "action": r.action,
                "score": r.score,
                "cost_tokens": r.cost_tokens,
                "latency_ms": r.latency_ms,
                "model": r.model,
                "degraded": bool(r.meta.get("degraded")),
            },
        )
        row = labels.get(f.name)
        if row:
            matrix[row["human_verdict"]][r.verdict] += 1
            human_action = row.get("human_next_action") or ""
            if human_action:
                action_total += 1
                action_agree += human_action == r.action
            if (r.verdict, r.action) in INVALID_COMBOS:
                invalid[f"{r.verdict}+{r.action}"] += 1

    avg = total / max(processed, 1)
    print(
        f"\n本次处理 {processed} 个;失败 {failures} 个;总花费: {total} tokens;"
        f"降级 {degraded} 个;平均 {avg:.0f} token/文件"
    )
    if not labels:
        print(f"[warn] 未找到 labels.csv,跳过对照(样本目录:{samples_root})")
        return
    print("混淆矩阵(行=human_verdict,列=G2_verdict):")
    for hv in ("accept", "partial", "reject"):
        row = matrix.get(hv, Counter())
        print(
            f"   {hv:<8} accept={row.get('accept', 0):<3} "
            f"partial={row.get('partial', 0):<3} reject={row.get('reject', 0):<3}"
        )
    human_ap = sum(matrix.get(v, Counter()).total() for v in ("accept", "partial"))
    false_reject = sum(matrix.get(v, Counter())["reject"] for v in ("accept", "partial"))
    if human_ap:
        print(f"G2 误拒率: {false_reject}/{human_ap} = {false_reject / human_ap:.1%}")
    if action_total:
        print(
            f"action 与人工一致: {action_agree}/{action_total} = {action_agree / action_total:.1%}"
        )
    print(f"非法 verdict+action 组合: {dict(invalid) if invalid else '无'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="G2 归属闸冒烟(默认 dry-run,不调 LLM)")
    ap.add_argument("--samples", required=True, help="样本目录路径")
    ap.add_argument("--labels", default=None, help="labels.csv 路径(默认向上找两级)")
    ap.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="只打印 prompt(默认行为)"
    )
    ap.add_argument("--live", action="store_true", help="真调 LLM(会花钱)")
    ap.add_argument(
        "--delay", type=float, default=3.0, help="live 模式每个文件之间的 sleep 秒数(默认 3)"
    )
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理 N 个文件")
    ap.add_argument("--resume", action="store_true", help="跳过已出结果的 rel_path")
    ap.add_argument(
        "--results", default=str(RESULTS_PATH), help="live 结果追加文件(默认 backend/tmp/)"
    )
    args = ap.parse_args()

    dry_run = not args.live

    root = Path(args.samples)
    if not root.is_dir():
        print(f"[skip] 样本目录不存在:{root}")
        return 0

    files = _ok_files(root)
    if not files:
        print(f"[skip] 目录里没有 ok 文件:{root}")
        return 0

    charter = get_settings().resolve_charter()
    if dry_run:
        subset = files[: args.limit] if args.limit else files
        print(
            f"[dry-run] 只检查 prompt 组装,不调用任何 LLM;charter {len(charter)} 字符;"
            f"文件 {len(subset)}/{len(files)}"
        )
        _dry_run(subset, charter)
    else:
        labels_path = _find_labels(root, args.labels)
        labels = _read_labels(labels_path)
        print(f"[live] labels={labels_path}(只读)")
        _live(
            files,
            charter,
            labels,
            root,
            delay=args.delay,
            limit=args.limit,
            resume=args.resume,
            results_path=Path(args.results),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
