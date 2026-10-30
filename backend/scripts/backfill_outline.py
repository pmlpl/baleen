#!/usr/bin/env python
"""存量笔记标题整理回填:默认 dry-run,只列候选清单,**一次 LLM 都不调**。

    cd backend && uv run python scripts/backfill_outline.py
    cd backend && uv run python scripts/backfill_outline.py --note-id 3 --live
    cd backend && uv run python scripts/backfill_outline.py --live
    cd backend && uv run python scripts/backfill_outline.py --live --resume --limit 5

- 默认(dry-run):列出该用户全部 active 笔记,标注「已整理过 / 结构已好可跳过 /
  待整理」,并给出标题数/编号覆盖率/正文字数 —— 解释每篇为什么处理或跳过。
- ``--live``:真调 LLM 整理标题,并删光该篇旧 chunks 重建+重嵌(单篇一个事务)。
  开头会打印**生效的模型** —— 标题整理不能用推理模型(8192 token 全花在
  reasoning,content 为空),确认是 deepseek-flash 之类再继续。
  - ``--note-id N``:只处理指定笔记(验收第一篇用)。
  - ``--limit N``:本次最多处理 N 篇(真正花 LLM 的才算数)。
  - ``--resume``:跳过结果文件里已成功(REWRITTEN)的 note_id,断点续跑。
  - ``--delay N``:每篇之间 sleep N 秒(默认 1,防网关限流)。
  - ``--llm-base-url/--llm-api-key/--llm-model``:临时覆盖 LLM 配置(只影响本次
    回填,DB 设置页与 .env 都不动)——DB 钉死推理模型时用它换 deepseek-flash。
- 结果追加在 ``backend/tmp/backfill_outline_results.jsonl``;幂等闸是
  sources.parse_meta.headings_rewritten,重复跑只会打印 skipped_done。
"""

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.service.embed import embed_passage  # noqa: E402
from app.service.llm import LLMClient  # noqa: E402
from app.service.llm.resolve import resolve_llm_config  # noqa: E402
from app.service.rewrite.backfill import (  # noqa: E402
    PROCESSED_STATUSES,
    REWRITTEN,
    BackfillOutcome,
    backfill_notes,
    list_candidates,
)

RESULTS_PATH = Path(__file__).resolve().parents[1] / "tmp" / "backfill_outline_results.jsonl"

_STATUS_MARK = {
    "rewritten": "done",
    "no_specs": "warn",
    "skipped_done": "skip",
    "skipped_structure": "skip",
    "failed": "err",
}


def _load_done(path: Path) -> set[int]:
    """--resume 用:已成功(rewritten)的 note_id 集合;no_specs/failed 不算,要重试。"""
    if not path.exists():
        return set()
    done: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("status") == REWRITTEN:
                done.add(int(row["note_id"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return done


def _append_result(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _short(text: str, width: int = 36) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


async def _dry_run(owner_id: int, note_ids: list[int] | None) -> None:
    async with session_factory() as session:
        candidates = await list_candidates(session, owner_id)
    if note_ids:
        candidates = [c for c in candidates if c["note_id"] in note_ids]
    if not candidates:
        print(f"[skip] owner={owner_id} 没有 active 笔记")
        return

    todo = done = good = 0
    for c in candidates:
        if c["already_done"]:
            mark, done = "skip", done + 1
        elif not c["needs_rewrite"]:
            mark, good = "skip", good + 1
        else:
            mark, todo = "todo", todo + 1
        ratio = (
            f"{c['numbered'] / c['headings']:.0%}" if c["headings"] else "-"
        )
        print(
            f"{mark:<4} note={c['note_id']:<4} {_short(c['title']):<38} "
            f"headings={c['headings']:<3} numbered={c['numbered']:<3}({ratio:<4}) "
            f"chars={c['body_chars']:<6}"
        )
    print(
        f"\n共 {len(candidates)} 篇:待整理 {todo},已整理过 {done},"
        f"结构已好可跳过 {good}"
    )
    print("[dry-run] 未调 LLM、未写库;确认清单后加 --live 执行")


async def _live(
    owner_id: int,
    note_ids: list[int] | None,
    *,
    delay: float,
    limit: int | None,
    resume: bool,
    results_path: Path,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
) -> None:
    async with session_factory() as session:
        base_url, api_key, model = await resolve_llm_config(session, owner_id)
        # 命令行覆盖优先(只影响本次运行,不写回 DB/.env)
        base_url = llm_base_url or base_url
        api_key = llm_api_key or api_key
        model = llm_model or model
        if not api_key:
            print(
                f"[skip] owner={owner_id} 没配 LLM api_key(设置页或 app_settings);"
                "key 不回退 .env,配好后再跑 --live"
            )
            return
        # 推理模型(glm-5.2 网关→sensenova)对长文档 content 返回空,发现不对先停下
        print(f"[live] 生效模型: model={model} base_url={base_url}")
        client = LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=get_settings().llm_long_task_timeout,
        )

        done = _load_done(results_path) if resume else set()
        if resume:
            print(f"[resume] 已成功 {len(done)} 篇:{results_path}")
        candidates = await list_candidates(session, owner_id)
        all_ids = [c["note_id"] for c in candidates]
        targets = [nid for nid in (note_ids if note_ids else all_ids) if nid not in done]
        if not targets:
            print("[skip] 没有要处理的笔记(全部已完成或被过滤)")
            return
        print(f"[throttle] delay={delay}s limit={limit or '全部'} 本次目标 {len(targets)} 篇")

        counter = Counter()

        def sink(outcome: BackfillOutcome) -> None:
            _append_result(results_path, outcome.as_row())
            counter[outcome.status] += 1
            mark = _STATUS_MARK.get(outcome.status, "?")
            detail = (
                f"+{outcome.headings_added} 标题, chunks {outcome.old_chunks}→"
                f"{outcome.new_chunks}, 向量 {outcome.embedded}"
                if outcome.status == REWRITTEN
                else outcome.error or ""
            )
            print(
                f"{mark:<4} note={outcome.note_id:<4} {_short(outcome.title):<38} "
                f"{outcome.status:<17} {detail}"
            )
            if outcome.status in PROCESSED_STATUSES and delay > 0:
                time.sleep(delay)

        await backfill_notes(
            session,
            owner_id,
            client=client,
            embed_func=embed_passage,
            note_ids=targets,
            limit=limit,
            on_result=sink,
        )

    print(f"\n本次结果:{dict(counter)}")
    print(f"结果文件:{results_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="存量笔记标题整理回填(默认 dry-run,不调 LLM)")
    ap.add_argument("--owner-id", type=int, default=1, help="按用户跑(默认 1)")
    ap.add_argument(
        "--note-id",
        type=int,
        action="append",
        default=None,
        help="只处理指定笔记(可重复传多个)",
    )
    ap.add_argument("--live", action="store_true", help="真调 LLM 并写库(会花钱)")
    ap.add_argument("--delay", type=float, default=1.0, help="live 每篇之间 sleep 秒数(默认 1)")
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理 N 篇")
    ap.add_argument("--resume", action="store_true", help="跳过已成功的 note_id")
    ap.add_argument("--results", default=str(RESULTS_PATH), help="live 结果追加文件")
    ap.add_argument("--llm-base-url", default=None, help="覆盖 LLM base_url(仅本次)")
    ap.add_argument("--llm-api-key", default=None, help="覆盖 LLM api_key(仅本次)")
    ap.add_argument("--llm-model", default=None, help="覆盖 LLM model(仅本次)")
    args = ap.parse_args()

    note_ids = args.note_id or None
    if args.live:
        asyncio.run(
            _live(
                args.owner_id,
                note_ids,
                delay=args.delay,
                limit=args.limit,
                resume=args.resume,
                results_path=Path(args.results),
                llm_base_url=args.llm_base_url,
                llm_api_key=args.llm_api_key,
                llm_model=args.llm_model,
            )
        )
    else:
        asyncio.run(_dry_run(args.owner_id, note_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
