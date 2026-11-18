#!/usr/bin/env python3
"""baleen / eval · AI 复核员(盲审)——单人标注的第二审。

问题:评测标签是单人判的,主观、无法自证。解法:AI **独立盲审**同一批文件
(不看人工标签),一致率 = 标注质量的客观证据,**分歧清单 = 主人唯一需要裁决的地方**。
AI 判卷不算标注(10-29 决定 11 的纪律不变)——人工仍是最终裁决者,分歧由人裁。

- 判分标准:rubric.md §0-§2 原文注入(三问 + 三档判据 + 反例清单 + 边界策略)。
- 盲审:脚本从不读 labels.csv 的判定列,直到 compare 阶段才对表。
- 图片/扫描件走 parse_file(client=) 视觉转写(与生产同链路,需视觉模型)。

用法(与其它脚本同一条规则,cd backend 后 uv run):
    uv run python ..\\eval\\scripts\\review_agent.py                  # 盲审 inputs/samples(35)并出一致率
    uv run python ..\\eval\\scripts\\review_agent.py --mode candidates
    uv run python ..\\eval\\scripts\\review_agent.py --compare       # 只重算一致率报告(不烧 LLM)

输出:
    eval/runs/ai_review.csv            盲审结果(samples 模式)
    eval/runs/ai_review_candidates.csv 盲审结果(candidates 模式)
    eval/runs/ai_agreement.md          一致率 + 分歧裁决单(samples 模式)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]  # eval/
BACKEND_DIR = EVAL_DIR.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

RUBRIC_PATH = EVAL_DIR / "rubric.md"
LABELS_PATH = EVAL_DIR / "inputs" / "labels.csv"
MODES = {
    "samples": {
        "root": EVAL_DIR / "inputs" / "samples",
        "out": EVAL_DIR / "runs" / "ai_review.csv",
        "agreement": True,
    },
    "candidates": {
        "root": EVAL_DIR / "inputs" / "candidates",
        "out": EVAL_DIR / "runs" / "ai_review_candidates.csv",
        "agreement": False,
    },
}
AGREEMENT_MD = EVAL_DIR / "runs" / "ai_agreement.md"

OWNER_ID = 1
CONTENT_BUDGET = 6000  # 喂给复核员的正文截断
# 推理模型(glm 系)会把输出预算先烧在 reasoning_content 上,预算太小 content 直接为空
# (2026-10-30 探针同款坑:probe 用 max_tokens=8 时推理模型 content 必空)——给足余量
MAX_TOKENS = 4000
VERDICTS = ("accept", "partial", "reject")
BUDGET_LINE = CONTENT_BUDGET // 2  # 头+尾各取一半,防「只有开头」


# ---------------------------------------------------------------- 判分标准


def rubric_core() -> str:
    """rubric §0-§2(判定尺度核心)原文。"""
    text = RUBRIC_PATH.read_text(encoding="utf-8")
    start, end = text.find("## 0."), text.find("\n## 3.")
    if start < 0 or end < 0:
        raise SystemExit("[error] rubric.md 结构变化,取不到 §0-§2 段")
    return text[start:end].strip()


def system_prompt(rubric: str) -> str:
    return (
        "你是 baleen 个人笔记库的**独立复核员**,对一份待入库文件做价值判定。"
        "判定标准如下(标注指南原文),严格按它执行:\n\n"
        f"{rubric}\n\n"
        "只输出一个 JSON 对象(不要多余文字):"
        '{"verdict":"accept|partial|reject","reason":"一句话理由(具体到缺什么/有什么,不写「质量低」)"}'
    )


# ---------------------------------------------------------------- LLM 配置


async def build_client():
    """owner 的 DB 配置优先,回退 .env/默认,环境变量最后覆盖(生产同语义)。"""
    import asyncpg

    from app.core.config import get_settings
    from app.service.llm import LLMClient

    s = get_settings()
    base_url, api_key, model = s.llm_base_url, s.llm_api_key, s.llm_model
    conn = await asyncpg.connect(s.database_url.replace("+asyncpg", ""))
    try:
        row = await conn.fetchrow(
            "SELECT llm_base_url, llm_api_key, llm_model FROM app_settings"
            " WHERE owner_id=$1",
            OWNER_ID,
        )
        if row:
            base_url = row["llm_base_url"] or base_url
            api_key = row["llm_api_key"] or api_key
            model = row["llm_model"] or model
    finally:
        await conn.close()
    base_url = os.environ.get("LLM_BASE_URL", base_url)
    api_key = os.environ.get("LLM_API_KEY", api_key)
    model = os.environ.get("LLM_MODEL", model)
    if not api_key:
        raise SystemExit("[error] 没配 LLM key(设置页配,或环境变量 LLM_API_KEY)")
    return LLMClient(base_url=base_url, api_key=api_key, model=model)


# ---------------------------------------------------------------- 盲审一个


def _clip(text: str) -> str:
    if len(text) <= CONTENT_BUDGET:
        return text
    return text[:BUDGET_LINE] + "\n……(中段截断)……\n" + text[-BUDGET_LINE:]


def _parse_verdict(content: str) -> tuple[str, str]:
    """从回复里抠 JSON;失败返回 unparsed。"""
    m = re.search(r"\{[^{}]*\}", content, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            verdict = str(obj.get("verdict", "")).strip().lower()
            reason = str(obj.get("reason", "")).strip()
            if verdict in VERDICTS:
                return verdict, reason
        except json.JSONDecodeError:
            pass
    return "unparsed", content[:120].replace("\n", " ")


def judge_one(client, path: Path, rel: str, rubric: str) -> dict:
    from app.service.parse import parse_file
    from app.service.parse.blocks import flatten_text

    t0 = time.perf_counter()
    try:
        pr = parse_file(path, client=client)  # client 注入:图片走视觉转写(与生产同链路)
    except Exception as exc:  # noqa: BLE001 单文件失败不中断
        return {"rel_path": rel, "ai_verdict": "unparsed", "ai_reason": f"parse 抛错:{exc}", "latency_ms": 0}
    if pr.parse_status.value != "ok":
        return {
            "rel_path": rel,
            "ai_verdict": "unreadable",
            "ai_reason": f"解析不了({pr.parse_status.value}):{pr.message or ''}",
            "latency_ms": 0,
        }
    text = flatten_text(pr.blocks) if pr.blocks.get("content") else ""
    if not text.strip():
        return {"rel_path": rel, "ai_verdict": "unreadable", "ai_reason": "解析 ok 但无正文", "latency_ms": 0}
    messages = [
        {"role": "system", "content": system_prompt(rubric)},
        {"role": "user", "content": (
            f"文件名:{path.name}\n扩展名:{path.suffix}  大小:{path.stat().st_size}B\n\n"
            f"内容(超长截断):\n{_clip(text)}"
        )},
    ]
    try:
        result = client.chat(messages, temperature=0.0, max_tokens=MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        return {"rel_path": rel, "ai_verdict": "unparsed", "ai_reason": f"LLM 调用失败:{exc}", "latency_ms": 0}
    if not result.content.strip():
        return {"rel_path": rel, "ai_verdict": "unparsed",
                "ai_reason": "LLM 返回空 content(推理模型?换 deepseek-flash)", "latency_ms": 0}
    verdict, reason = _parse_verdict(result.content)
    return {
        "rel_path": rel,
        "ai_verdict": verdict,
        "ai_reason": reason,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


# ---------------------------------------------------------------- 一致率


def write_agreement(ai_rows: list[dict]) -> int:
    """samples 模式:盲审结果 × labels.csv → 一致率 + 分歧裁决单。"""
    with LABELS_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        labels = {r["rel_path"]: r for r in csv.DictReader(fh)}

    comparable: list[tuple[dict, dict]] = []
    excluded: list[dict] = []
    for a in ai_rows:
        h = labels.get(a["rel_path"])
        if h is None:
            continue
        if a["ai_verdict"] not in VERDICTS:
            excluded.append({**a, "human_verdict": h.get("human_verdict", "")})
        else:
            comparable.append((a, h))

    exact = sum(1 for a, h in comparable if a["ai_verdict"] == h["human_verdict"])
    # 方向一致:同收(accept/partial)或同拒(reject)——partial 与 accept 算同向
    directional = sum(
        1 for a, h in comparable
        if (a["ai_verdict"] in ("accept", "partial")) == (h["human_verdict"] in ("accept", "partial"))
    )
    disagrees = [(a, h) for a, h in comparable if a["ai_verdict"] != h["human_verdict"]]

    n = len(comparable)
    pct_exact = f"{exact / n:.1%}" if n else "N/A(无可比条目)"
    pct_dir = f"{directional / n:.1%}" if n else "N/A"
    lines = [
        "# AI 复核员 · 一致率报告(盲审)",
        "",
        f"> 生成:{datetime.now().isoformat(timespec='seconds')} · 模式:samples · 可比 {n} 条"
        f"(排除 {len(excluded)} 条:解析不了/模型没给判定) · **人工仍是最终裁决者**",
        "",
        "## 结论",
        "",
        f"- **完全一致 {exact}/{n} = {pct_exact}**(三档 verdict 逐字相同)",
        f"- **方向一致 {directional}/{n} = {pct_dir}**(同收/同拒,partial 与 accept 视为同向)",
        f"- **分歧 {len(disagrees)} 条** → 下面裁决单,主人逐条裁决(这是唯一需要人看的地方)",
        "",
        "解读:完全一致率衡量 AI 与人像不像一个标注者;分歧不是谁错了,是**主观边界案件**,"
        "裁决结果回流 labels.csv 才算闭环。",
        "",
        "## 混淆表(人 × AI)",
        "",
        "| 人\\AI | accept | partial | reject |",
        "|---|---|---|---|",
    ]
    if n == 0:
        print(f"[warn] 可比条目为 0(排除 {len(excluded)} 条)——全部不可比,先看排除项")
    for hv in VERDICTS:
        counts = [sum(1 for a, h in comparable if h["human_verdict"] == hv and a["ai_verdict"] == av)
                  for av in VERDICTS]
        lines.append(f"| {hv} | {counts[0]} | {counts[1]} | {counts[2]} |")

    if disagrees:
        lines += ["", "## 分歧裁决单(主人填「最终裁决」列)", "",
                  "| rel_path | 你的判定(理由) | AI 判定(理由) | 最终裁决 |", "|---|---|---|---|"]
        for a, h in disagrees:
            hreason = (h.get("human_reason") or "")[:40]
            areason = (a["ai_reason"] or "")[:40]
            lines.append(f"| `{a['rel_path']}` | **{h['human_verdict']}**({hreason}) "
                         f"| **{a['ai_verdict']}**({areason}) |  |")
    if excluded:
        lines += ["", "## 排除项(解析不了/模型未给判定,不进一致率)", ""]
        lines += [f"- `{e['rel_path']}`:{e['ai_verdict']} {e['ai_reason'][:60]}" for e in excluded]

    AGREEMENT_MD.parent.mkdir(parents=True, exist_ok=True)
    AGREEMENT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] 一致率:完全 {exact}/{n}({exact / n:.1%}) 方向 {directional}/{n};"
          f"分歧 {len(disagrees)} 条 → {AGREEMENT_MD}")
    return 0


# ---------------------------------------------------------------- 入口


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 复核员:盲审 + 一致率 + 分歧裁决单")
    ap.add_argument("--mode", choices=("samples", "candidates"), default="samples")
    ap.add_argument("--compare", action="store_true",
                    help="只重算一致率报告(读已有盲审 csv,不烧 LLM;仅 samples 模式)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resume", action="store_true", help="跳过 out csv 里已有判定的 rel_path")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()

    mode = MODES[args.mode]
    out_path, root = mode["out"], mode["root"]
    if not root.is_dir():
        print(f"[error] 目录不存在:{root}", file=sys.stderr)
        return 2

    if args.compare:
        if args.mode != "samples" or not out_path.exists():
            print("[error] --compare 仅 samples 模式且已有盲审 csv", file=sys.stderr)
            return 2
        with out_path.open("r", encoding="utf-8-sig", newline="") as fh:
            write_agreement(list(csv.DictReader(fh)))
        return 0

    files = sorted(
        (p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.relative_to(root).as_posix().lower(),
    )
    if args.limit:
        files = files[: args.limit]
    print(f"[review] 模式={args.mode} 文件 {len(files)} 个(盲审:不看人工标签)")

    from app.service.llm import LLMError

    try:
        client = asyncio.run(build_client())
        caps = client.probe()
        print(f"probe: model={caps.model} chat={caps.chat} json_mode={caps.json_mode} {caps.notes}")
    except (SystemExit, LLMError) as exc:
        print(f"[error] LLM 不可用:{exc}", file=sys.stderr)
        return 2

    rubric = rubric_core()
    rows: list[dict] = []
    done: set[str] = set()
    if args.resume and out_path.exists():
        with out_path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        done = {r["rel_path"] for r in rows if r.get("ai_verdict") in VERDICTS}
        print(f"[resume] 已有判定 {len(done)} 条")

    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in done:
            continue
        row = judge_one(client, path, rel, rubric)
        rows.append(row)
        print(f"  {rel[:44]:<46} {row['ai_verdict']:<10} {row['ai_reason'][:40]}")
        time.sleep(args.delay)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["rel_path", "ai_verdict", "ai_reason", "latency_ms"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[ok] {len(rows)} 行 → {out_path}")

    if mode["agreement"]:
        write_agreement(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
