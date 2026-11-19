"""帧选择质量评测（backlog #6）的判定逻辑测试。

测的是「题集打分逻辑对不对」，不是真库数据：
- keep / drop 的容差必须分开（drop 3s 精确、keep 20s 容漂移）
- 「张数下限」类条目（召回/确定性基线）不依赖特定画面
- 零帧必须能被识别
- 汇总指标算法正确

为什么专门测这个:2026-11-19 首跑时题集自己错了 5 条（alt 关键词写错、keep 与 drop
共用 20s 容差导致假红 4 张）——这类错误会让评测面**报出假缺陷**，比不评测更糟。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "eval" / "scripts" / "frame_eval.py"
assert _SCRIPT.exists(), f"找不到评测脚本: {_SCRIPT}"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("frame_eval_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ 必须先注册进 sys.modules 再 exec_module：frame_eval.py 用了 @dataclass，
    # dataclasses 处理字段注解时会做 sys.modules.get(cls.__module__).__dict__，
    # 模块没注册就拿到 None → AttributeError: 'NoneType' object has no attribute '__dict__'
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


F = _load()


def _hit(ts: float | None, alt: str = "") -> object:
    return F.FrameHit(note=1, ts=ts, alt=alt)


# ---------------------------------------------------------------- 容差分开


def test_drop_uses_tight_tolerance() -> None:
    """drop 条目 ±3s：33.9s 的坏帧不该把 44.8s 的无关帧也算进来。

    统一用 20s 时,6015「通货膨胀帧(33.9)」会把 18.9/23.2/44.8 三张也算命中 →
    假红 4 张。这条测试钉住「drop 必须用紧容差」。
    """
    exp = F.Expectation(note=1, label="坏帧", expect="drop", category="x", ts=33.9)
    assert F._matches(exp, _hit(33.9)) is True       # 正好命中
    assert F._matches(exp, _hit(35.5)) is True       # ±3s 内
    assert F._matches(exp, _hit(44.8)) is False      # 20s 内但超出 3s → 不算
    assert F._matches(exp, _hit(18.9)) is False


def test_keep_uses_wide_tolerance() -> None:
    """keep 条目 ±20s：三候选采样会把同场景时间点从 189.5 漂到 205 左右，仍算命中。"""
    exp = F.Expectation(note=1, label="豆包帧", expect="keep", category="x", ts=189.5)
    assert F._matches(exp, _hit(189.5)) is True
    assert F._matches(exp, _hit(205.0)) is True      # 漂 15.5s 仍算
    assert F._matches(exp, _hit(230.0)) is False     # 漂太远


def test_alt_keyword_fallback() -> None:
    """给了 alt_kw 时关键词也算命中（时间戳完全未知的场景）。"""
    exp = F.Expectation(note=1, label="NestJS", expect="drop", category="x", alt_kw="NestJS")
    assert F._matches(exp, _hit(None, "NestJS 出场时叠加的画面")) is True
    assert F._matches(exp, _hit(None, "完全无关的帧")) is False


# ---------------------------------------------------------------- 张数下限


def test_min_frames_without_kw_ignores_content() -> None:
    """「至少 N 帧」类条目只看张数，不要求任何特定画面。"""
    exp = F.Expectation(
        note=1, label="基线", expect="keep", category="x", min_frames_without_kw=5
    )
    frames = {1: [_hit(float(i), "任意描述") for i in range(5)]}
    results = F.evaluate(frames, [exp])
    assert len(results) == 1 and results[0].ok is True

    fewer = {1: [_hit(float(i), "任意描述") for i in range(4)]}
    results = F.evaluate(fewer, [exp])
    assert results[0].ok is False


# ---------------------------------------------------------------- 零帧


def test_zero_frames_flagged() -> None:
    """6014 类：keep 条目一张都没命中 → 该笔记进零帧名单。"""
    exps = [
        F.Expectation(note=6014, label="动画帧", expect="keep", category="零帧",
                      min_frames_without_kw=1),
    ]
    frames = {6014: []}
    results = F.evaluate(frames, exps)
    summary = F.summarize(results)
    assert 6014 in summary["零帧笔记"]


def test_missing_note_counts_as_zero_frames() -> None:
    """笔记不在库里(doc 为空)也按零帧算——不能因为读不到就当作通过。"""
    exps = [
        F.Expectation(note=9999, label="x", expect="keep", category="零帧",
                      min_frames_without_kw=1),
    ]
    results = F.evaluate({9999: []}, exps)
    assert results[0].ok is False


# ---------------------------------------------------------------- 汇总指标


def test_summary_rates() -> None:
    """混入率 = 仍有混入的 drop 条目 / drop 条目总数；召回率同理。"""
    exps = [
        F.Expectation(1, "混入1", "drop", "c", alt_kw="坏"),
        F.Expectation(1, "已清2", "drop", "c", alt_kw="也坏"),
        F.Expectation(1, "该留", "keep", "c", alt_kw="好"),
        F.Expectation(2, "该留但没留", "keep", "c", alt_kw="缺失"),
    ]
    frames = {
        1: [_hit(1.0, "这里有坏帧"), _hit(2.0, "这里有好帧")],
        2: [],
    }
    results = F.evaluate(frames, exps)
    s = F.summarize(results)
    assert s["无用帧混入率"].startswith("1/2"), s["无用帧混入率"]  # 1 个 drop 仍混入
    assert s["有用帧召回率"].startswith("1/2"), s["有用帧召回率"]  # 1 个 keep 命中
    assert 2 in s["零帧笔记"]


def test_any_expectation_is_skipped() -> None:
    """主人未表态（any）的条目不做断言，避免把「没意见」当「必须保留」。"""
    exps = [F.Expectation(1, "未表态", "any", "c", alt_kw="随便")]
    results = F.evaluate({1: []}, exps)
    assert results == []


# ---------------------------------------------------------------- 题集自检


def test_expectation_corpus_is_sane() -> None:
    """题集本身的自检（preflight 精神）：每条 drop/keep 至少有一个判据。

    没有 ts 也没有 alt_kw 的 drop 条目、且 min_frames_without_kw=0 的 keep 条目
    等于「永远命中不了任何东西」——那是坏题。
    """
    for exp in F.EXPECTATIONS:
        if exp.expect == "any":
            continue
        has_ts = exp.ts is not None
        has_kw = bool(exp.alt_kw)
        has_count = exp.min_frames_without_kw > 0
        assert has_ts or has_kw or has_count, f"坏题（无任何判据）: {exp.label}"


def test_expectation_notes_are_known() -> None:
    """题集里出现的 note id 必须都是 11-19 复核过的那些（防手滑写错 id）。"""
    known = {6009, 6011, 6012, 6013, 6014, 6015, 6090, 6094, 6095, 6096}
    used = {e.note for e in F.EXPECTATIONS}
    assert used <= known, f"未知 note id: {used - known}"
