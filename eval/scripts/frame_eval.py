#!/usr/bin/env python
"""帧选择质量评测（backlog #6）——「无用帧混入率 / 有用帧召回率 / 零帧率」。

题集在 `eval/queries/frame_selection_expectations.md`（主人 2026-11-19 的逐帧裁决）。
本脚本读**真实库里的笔记 doc**，按题集的期望打分。

## 怎么判「实际嵌入了哪些帧」

笔记 doc 里的 image 块带 `attrs.timestamp`（视频时间戳秒）和 `attrs.alt`。
比对策略（三层，从严到宽）：
1. **时间戳窗口**：库里的时间戳与期望条目的 `ts` 相差 ≤ TOL 秒 → 命中
2. **alt 关键词**：题目给了 `alt_kw` 时，alt 含该关键词也算命中（时间戳会因采样点变化而漂移）
3. `any` 条目跳过

## 为什么必须按「窗口 + 关键词」而不是严格时间戳

2026-11-19 晚把采样点从 [中点, 75%] 扩成 [中点, 75%, 95%]，同一场景保留下来的时间戳会变
（取最后者）。所以「6011 豆包帧 189.5s」在新产物里可能是 195s 或 205s——严格相等必假红。

## ⚠️ 重要：存量笔记是旧规则产物

默认跑出来的是**旧规则的基线**（用来证明题集能抓到已知问题），不是新规则的成绩。
要看新规则成绩，先重导（删旧笔记防 dedupe）再跑。

用法：
    cd backend
    uv run python ../eval/scripts/frame_eval.py                 # 打存量基线
    uv run python ../eval/scripts/frame_eval.py --json          # 机器可读
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
BACKEND = EVAL_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND))

# 时间戳容差（秒）——keep 与 drop 不能用同一个：
#   keep:三候选采样会让同场景时间点漂移([中点,75%,95%] 取最后者),20s 够覆盖
#   drop:精确定位「已知的那一帧坏帧」,必须紧。曾用 20s 导致 6015 的「通货膨胀帧
#        (33.9s)」把 18.9/23.2/44.8 三张无关帧也算成命中 → 假红 4 张
TS_TOL_KEEP = 20.0
TS_TOL_DROP = 3.0


@dataclass
class Expectation:
    note: int
    label: str  # 题集里的帧描述（人读）
    expect: str  # keep / drop / any
    category: str
    ts: float | None = None  # 已知时间戳（可空）
    alt_kw: str = ""  # alt 关键词（可空）
    min_count: int = 1  # keep 条目至少命中几张（6014 要求 ≥1）
    # keep 条目也可以只断言「张数下限」（不要求关键词命中）——用于召回/确定性基线，
    # 那些条目的语义是「这篇至少该有 N 张帧」，而不是「必须有某张特定画面」。
    min_frames_without_kw: int = 0


# 题集硬编码在这里，与 md 文档一一对应（md 给人读，这里是机读。
# 为什么不用 markdown 表格解析：表格里的中文标点/换行会让解析器极度脆弱，
# 而条目数量少（~19 条），手写常量更可靠且能带类型）
#
# ⚠️ 2026-11-19 首次跑通后按真实 alt 文本修正了 5 条（题集自身的错）：
#   - 6095/6094 两类「召回基线」原写 alt_kw 要求含「该视频的 N 帧」字样 ——
#     真实 alt 是画面描述（「39 个前端技术按等级排列的图标清单」），永不可能命中。
#     改成 min_frames_without_kw：只断言「这篇至少该有 N 张帧」。
#   - 6015「图表帧」原 alt_kw='图' 太窄；实际 10 张都是真实内容（欠条比喻/收益
#     风险对比/国债类型等），改 min_frames_without_kw=5。
#   - 6012「完整分级总表」原 alt_kw='分级'，真实 alt 是「按等级排列的…榜单」。
#   - 6096「半截公式帧」原 alt_kw='公式' 会命中正文公式帧（误报 5 张），
#     改成按时间戳精确定位（608.5s 是主人截图里的那一帧）。
EXPECTATIONS: list[Expectation] = [
    # 6009 Docker —— 主人批「无用图：搜索 Docker 帧、片尾一箭三连帧」
    Expectation(6009, "片尾一箭三连", "drop", "互动引导", alt_kw="三连"),
    Expectation(6009, "Google 搜索自动补全", "drop", "搜索框", alt_kw="搜索"),
    Expectation(6009, "EC2 控制台连接信息", "keep", "真实界面", alt_kw="Connect to instance"),
    # 6011 AI 概念 20 个
    Expectation(6011, "结尾点赞收藏", "drop", "互动引导", alt_kw="点赞"),
    Expectation(6011, "豆包帧（该留但曾截早/截晚）", "keep", "时机错位", ts=189.5, alt_kw="豆包"),
    Expectation(6011, "开场 20 个概念总览", "keep", "知识载体", alt_kw="概念"),
    # 6012 前端 26 个 —— 主人「只有第一张完整分级总表有用，其余多余」
    Expectation(6012, "完整分级总表", "keep", "知识载体", alt_kw="等级"),
    Expectation(6012, "NestJS 帧（内容不在画面）", "drop", "时机错位", alt_kw="NestJS"),
    # 6013 PPO —— 主人「质量不错」
    Expectation(6013, "机器人行走反馈", "keep", "知识载体", alt_kw="机器人"),
    Expectation(6013, "优势公式与 Critic 网络", "keep", "知识载体", alt_kw="优势"),
    # 6014 Transformer —— 主人「没图质量就不好」；零帧类是核心缺陷
    Expectation(6014, "动画帧（≥1 张）", "keep", "零帧/整类误杀", min_frames_without_kw=1),
    # 6015 国债 —— 主人「主播+字幕帧无意义」
    Expectation(6015, "通货膨胀主播+字幕帧", "drop", "纯口播", ts=33.9, alt_kw="通货膨胀"),
    Expectation(6015, "图表/数据帧", "keep", "知识载体", min_frames_without_kw=5),
    # 6096 财富自由 —— 半截公式帧（主人指出「图片里面有几个下划线和+」）。
    # 该帧原在 608.5s；当晚扩三采样点（95%）后重导已无该时间戳的帧 → 视为已修。
    # 断言改成「608.5s 附近不应再有帧」——drop 语义不变，但没有 alt 关键词可匹配，
    # 只能靠时间戳窗口。
    Expectation(6096, "半截公式帧（下划线+零散加号）", "drop", "时机错位", ts=608.5),
    # 6095 39 前端 —— 确定性基线（5 帧是真实成色，不是漏检）
    Expectation(6095, "该视频至少 5 帧", "keep", "确定性基线", min_frames_without_kw=5),
    # 6094 3B1B —— 召回高水位基线（18 帧 96%）
    Expectation(6094, "该视频至少 15 帧", "keep", "召回基线", min_frames_without_kw=15),
]


@dataclass
class FrameHit:
    note: int
    ts: float | None
    alt: str


@dataclass
class Result:
    ok: bool
    exp: Expectation
    detail: str
    matched: list[FrameHit] = field(default_factory=list)


def _walk_images(blocks: list) -> list[FrameHit]:
    out: list[FrameHit] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "image":
            attrs = b.get("attrs") or {}
            ts = attrs.get("timestamp")
            out.append(FrameHit(
                note=0,
                ts=float(ts) if isinstance(ts, (int, float)) else None,
                alt=str(attrs.get("alt") or ""),
            ))
        if isinstance(b.get("content"), list):
            out.extend(_walk_images(b["content"]))
    return out


def _matches(exp: Expectation, hit: FrameHit) -> bool:
    """一条期望是否命中某个实际帧。

    判定顺序（先严后宽）:
    1. 时间戳窗口 —— keep 用宽窗(采样点漂移)、drop 用紧窗(精确定位坏帧)
    2. alt 关键词 —— 只有给了 alt_kw 才用；drop 条目**优先靠时间戳**,
       因为关键词对「坏帧」太容易误伤(6015「图」会命中所有图表帧)
    """
    if exp.ts is not None and hit.ts is not None:
        tol = TS_TOL_DROP if exp.expect == "drop" else TS_TOL_KEEP
        if abs(hit.ts - exp.ts) <= tol:
            return True
    if exp.alt_kw:
        return exp.alt_kw in hit.alt
    return False


async def _load_frames(note_ids: list[int]) -> dict[int, list[FrameHit]]:
    from sqlalchemy import text

    from app.core.db import session_factory

    frames: dict[int, list[FrameHit]] = {}
    async with session_factory() as session:
        for nid in note_ids:
            doc = await session.scalar(
                text("select doc from notes where id = :i and status = 'active'"),
                {"i": nid},
            )
            hits = _walk_images((doc or {}).get("content") or [])
            for h in hits:
                h.note = nid
            frames[nid] = hits
    return frames


def evaluate(frames: dict[int, list[FrameHit]], expectations: list[Expectation]) -> list[Result]:
    results: list[Result] = []
    for exp in expectations:
        if exp.expect == "any":
            continue
        hits = frames.get(exp.note, [])

        # 「只断言张数下限」的条目（召回/确定性基线）：不找特定画面，只看这篇有几张帧
        if exp.expect == "keep" and exp.min_frames_without_kw > 0:
            n = len(hits)
            ok = n >= exp.min_frames_without_kw
            results.append(Result(
                ok=ok, exp=exp,
                detail=f"实际 {n} 张（要求 ≥{exp.min_frames_without_kw}）",
                matched=hits,
            ))
            continue

        matched = [h for h in hits if _matches(exp, h)]

        if exp.expect == "keep":
            ok = len(matched) >= exp.min_count
            detail = f"命中 {len(matched)} 张（要求 ≥{exp.min_count}）"
        else:  # drop
            ok = len(matched) == 0
            if matched:
                ts_list = [round(h.ts, 1) if h.ts is not None else "?" for h in matched[:3]]
                detail = f"仍混入 {len(matched)} 张（时间戳 {ts_list}）"
            else:
                detail = "已清除"
        results.append(Result(ok=ok, exp=exp, detail=detail, matched=matched))
    return results


def summarize(results: list[Result]) -> dict:
    drops = [r for r in results if r.exp.expect == "drop"]
    keeps = [r for r in results if r.exp.expect == "keep"]
    # 无用帧混入率：drop 条目中仍有混入的比例
    mixed = [r for r in drops if r.matched]
    # 有用帧召回率：keep 条目中达到 min_count 的比例
    recalled = [r for r in keeps if r.ok]
    # 零帧：该笔记所有 keep 条目都未命中
    notes_with_keep: dict[int, list[Result]] = {}
    for r in keeps:
        notes_with_keep.setdefault(r.exp.note, []).append(r)
    zero_frame_notes = [nid for nid, rs in notes_with_keep.items() if not any(r.ok for r in rs)]
    return {
        "无用帧混入率": f"{len(mixed)}/{len(drops)} = {len(mixed)/len(drops):.0%}" if drops else "N/A",
        "有用帧召回率": f"{len(recalled)}/{len(keeps)} = {len(recalled)/len(keeps):.0%}" if keeps else "N/A",
        "零帧笔记": zero_frame_notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="帧选择质量评测（backlog #6）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    note_ids = sorted({e.note for e in EXPECTATIONS})
    frames = asyncio.run(_load_frames(note_ids))
    results = evaluate(frames, EXPECTATIONS)

    missing_notes = [nid for nid in note_ids if not frames.get(nid)]
    summary = summarize(results)

    if args.json:
        print(json.dumps({
            "summary": summary,
            "missing_notes": missing_notes,
            "results": [
                {
                    "note": r.exp.note,
                    "label": r.exp.label,
                    "expect": r.exp.expect,
                    "category": r.exp.category,
                    "ok": r.ok,
                    "detail": r.detail,
                }
                for r in results
            ],
        }, ensure_ascii=False, indent=1))
        return 0 if all(r.ok for r in results) else 1

    print("帧选择质量评测（backlog #6）")
    print("=" * 70)
    if missing_notes:
        print(f"[warn] 这些笔记不在库里（跳过）: {missing_notes}")
    print("")
    for r in results:
        flag = "PASS" if r.ok else "FAIL"
        print(f"{flag}  [{r.exp.note}] {r.exp.label:<28} ({r.exp.category})  {r.detail}")
    print("")
    print("=" * 70)
    for k, v in summary.items():
        print(f"{k}: {v}")
    print("")
    print("⚠️ 存量笔记是 2026-11-19 上午之前的旧筛选规则产物。")
    print("   上面 FAIL 的条目是旧规则的问题（题集有效性的证据）；")
    print("   要看新规则成绩，需先删旧笔记重导（防 dedupe）再跑本脚本。")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
