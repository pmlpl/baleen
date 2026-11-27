"""bootstrap 置信区间的统计正确性测试（D 路线，2026-11-19 补）。

起点是主人的一个问题:「误拒率一定要 0% 才算合格吗?」——查下来发现
**脚本本身在零事件时会谎报**：0/47 误拒报成 `[0%, 0%]`（等于宣称真实率必然是 0），
而正确的 95% 区间上界是 7.5%。这批测试把修复钉住。

测的是**统计口径**，不是格式化：
- 零事件 → CP 精确区间必须给出非零上界（bootstrap 在这里说谎）
- 全事件 → CP 上界必须是 100%，下界明显 > 0（bootstrap 会报 [100%,100%]）
- 正常比例 → CP 与 scipy 一致（下面有独立校验，不 import scipy 跑测试）
- 分母为 0 → 必须渲染成 N/A，不能崩（dev 没有 topic_dialog 样本时踩过）
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

# eval/scripts 不在 backend 包内,按路径加载
# tests/test_bootstrap_ci.py → parents[0]=tests, [1]=backend, [2]=baleen
_SCRIPT = Path(__file__).resolve().parents[2] / "eval" / "scripts" / "bootstrap.py"
assert _SCRIPT.exists(), f"找不到评测脚本: {_SCRIPT}"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("eval_bootstrap", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load()


# ---------------------------------------------------------------- CP 精确区间


@pytest.mark.parametrize(
    ("k", "n", "expect_upper"),
    [
        (0, 17, 0.195064),  # test ok 口径:0/17
        (0, 47, 0.075486),  # 全量 ok 口径:0/47
        (0, 30, 0.115703),  # dev ok 口径:0/30
        (3, 50, 0.165482),  # 全量全口径:3/50
        (1, 48, 0.110696),  # 改判前的 1/48
    ],
)
def test_cp_upper_known_values(k: int, n: int, expect_upper: float) -> None:
    """CP 上界与 scipy.stats.beta.ppf 的公认值对得上（容差 1e-4）。"""
    lo, hi = B.clopper_pearson(k, n)
    assert lo == pytest.approx(0.0 if k == 0 else lo, abs=1e-9)
    assert hi == pytest.approx(expect_upper, abs=1e-4)


def test_cp_zero_events_has_nonzero_upper() -> None:
    """零事件：上界必须明显 > 0 —— 这是整个修复的核心。

    旧 bootstrap 在这里返回 (0.0, 0.0)，等于宣称「误拒率必然是 0%」。
    """
    _, hi = B.clopper_pearson(0, 47)
    assert hi > 0.05, f"零事件上界不能是 0（实际 {hi}）"


def test_cp_all_events_symmetric() -> None:
    """全事件（k=n）：上界恒 1、下界明显 > 0；与 k=0 镜像。"""
    lo, hi = B.clopper_pearson(47, 47)
    assert hi == pytest.approx(1.0)
    assert lo > 0.90
    # 镜像性质：CP(k,n) 的上下界 = 1 - CP(n-k,n) 的上下界反转
    lo0, hi0 = B.clopper_pearson(0, 47)
    assert lo == pytest.approx(1 - hi0, abs=1e-9)


def test_cp_rejects_bad_input() -> None:
    """n<=0 时返回最宽区间，不抛异常（不该让空 split 崩脚本）。"""
    assert B.clopper_pearson(0, 0) == (0.0, 1.0)


# ---------------------------------------------------------------- 零事件切换


def test_percentile_ci_empty_returns_nan() -> None:
    """所有重采样都分母为 0 → 返回 nan（渲染成 N/A），不能 IndexError。

    dev split 的 ok 口径下没有 topic_dialog 样本，这里以前会直接崩。
    """
    lo, hi = B.percentile_ci([])
    assert math.isnan(lo) and math.isnan(hi)


def test_fmt_renders_na_for_nan() -> None:
    """nan 必须渲染成「N/A（无该类样本）」，不能显示成 0.0%。"""
    row = B._fmt(float("nan"), float("nan"), float("nan"), 0, "—")
    assert "N/A" in row[0]
    assert row[1] == "N/A"


def test_metric_ci_uses_cp_for_zero_denom() -> None:
    """分母 0 → 返回 nan 且不崩。"""
    point, lo, hi, n_shown, method = B.metric_ci(n_event=0, n_denom=0, point=0.0)
    assert math.isnan(point) and n_shown == 0


def test_metric_ci_zero_event_uses_cp() -> None:
    """0/47 走 CP：上界 7.5%，不是 bootstrap 的 0%。"""
    point, lo, hi, n_shown, method = B.metric_ci(n_event=0, n_denom=47, point=0.0)
    assert method == "CP 精确"
    assert point == 0.0
    assert lo == 0.0
    assert hi > 0.05
    assert n_shown == 47


def test_metric_ci_all_event_uses_cp() -> None:
    """35/35 走 CP：上界 100%、下界 ≈ 90%（bootstrap 会给 [100%,100%]）。

    精确值：CP(35,35) 下界 = 0.89997（= 0.025^(1/35) 的形式），所以阈值取 0.89
    而不是 0.90 —— 差一点点但意义完全不同：bootstrap 给的是「100% 必然」，
    CP 给的是「至少 90%」。
    """
    point, lo, hi, n_shown, method = B.metric_ci(n_event=35, n_denom=35, point=1.0)
    assert method == "CP 精确"
    assert hi == pytest.approx(1.0)
    assert lo > 0.89
    assert lo < 0.91  # 精确值 0.89997，钉住具体数值防漂移


def test_metric_ci_normal_proportion_also_uses_cp() -> None:
    """正常比例（33/35）同样用 CP —— 保证同一张表口径统一。

    旧实现的 if 只在 0 或 n 时切 CP，正常比例用 bootstrap，
    导致同表混用两种方法（答辩会被追问为什么口径不一致）。
    """
    point, lo, hi, n_shown, method = B.metric_ci(n_event=33, n_denom=35, point=33 / 35)
    assert method == "CP 精确"
    assert 0.80 < lo < 0.95
    assert 0.95 < hi <= 1.0
    # 上界不能是硬顶 100%（bootstrap 在此会顶到 100%）
    assert hi < 1.0 or hi == pytest.approx(0.9997, abs=1e-3)


# ---------------------------------------------------------------- 检索模式


def test_retrieval_recall_uses_cp(tmp_path: Path) -> None:
    """检索 Recall@k 也走 CP（与判定指标同口径），MRR 仍走 bootstrap。"""
    import json

    p = tmp_path / "pq.json"
    p.write_text(
        json.dumps({"per_query": [{"kind": "positive", "hit_rank": 1} for _ in range(10)]}),
        encoding="utf-8",
    )
    table = B.retrieval_metrics(p)
    body = table[1:]
    methods = {row[0]: row[4] for row in body}
    assert methods["Recall@5"] == "CP 精确"
    assert methods["Recall@10"] == "CP 精确"
    assert methods["MRR@10"] == "bootstrap"
    # 10/10 命中 → 上界 100%、下界 > 60%
    recall5 = next(row for row in body if row[0] == "Recall@5")
    assert recall5[1] == "100.0%"
    assert "100.0%" in recall5[2].split(", ")[1]
