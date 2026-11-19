#!/usr/bin/env python3
"""baleen / eval · bootstrap 置信区间(D 路线:统计严谨性)。

动机(主人 2026-11-02):n=35 样本 / 20 query 上只报点估计,100% 的 R@5 置信区间宽得
经不起论文答辩追问。本脚本用 **有放回重采样 + 百分位法** 给所有已报告的指标补 95% CI——
n 小不可怕,可怕的是只报点估计;样本数 + CI 才是诚实的报告。

两种模式:
1. G1/G2 判定指标(默认):labels.csv + preds.csv,对配对样本重采样;
   误拒率 / 误丢率 / 话题召回率 / 判定准确率 / macro-F1。
   --split test / dev 各跑一遍 = 交叉验证视角(test 18 / dev 17,不共享样本)。
2. 检索指标:ablation.py --json 的 per_query 输出,对 positives 的 hit_rank 重采样;
   Recall@5 / Recall@10 / MRR@10 / MRR。

方法学(论文可直接引用):B=2000 次有放回重采样,seed=42 可复现,95% 百分位区间;
分母为 0 的轮次不计入(单独报有效轮数)。

**零事件特例(2026-11-19 修)**:某组样本里一次都没出现待测事件(如 0/47 误拒)时,
百分位 bootstrap 会退化成 [0%, 0%] —— 2000 次重采样几乎每次都抽到 0 次事件,
分位点全是 0。那个区间在统计上等于宣称「真实率必然是 0」,答辩会被追问。
这种情况改用 **Clopper-Pearson 精确区间**(二项分布反解,不依赖重采样),
并在输出里把方法列标成 `CP 精确`。非零事件仍用 bootstrap(两者都是 95% 双侧口径,
所以在区间上下界的解释上是可比的)。

用法:
    python eval/scripts/bootstrap.py --split test
    python eval/scripts/bootstrap.py --split dev
    python eval/scripts/bootstrap.py --per-query eval/runs/ablation_perquery.json
    python eval/scripts/bootstrap.py --split test --out eval/runs/ci_test.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections.abc import Callable
from pathlib import Path

# 路径锚定脚本自身位置:从任何目录跑都不漂移
EVAL_DIR = Path(__file__).resolve().parents[1]

N_BOOT = 2000
SEED = 42
CI_LEVEL = 0.95

VERDICTS = ("accept", "partial", "reject")
QUALITY_RANK = {"core": 4, "normal": 3, "reference": 2, "junk": 1}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def percentile_ci(samples: list[float]) -> tuple[float, float]:
    if not samples:
        # 全部重采样都分母为 0（如该 split 里没有 topic_dialog 样本）
        # → 该指标不可估。返回 nan,调用方会渲染成「N/A」而不能假装是区间。
        return float("nan"), float("nan")
    ordered = sorted(samples)
    lo_q = (1 - CI_LEVEL) / 2 * 100
    hi_q = (1 + CI_LEVEL) / 2 * 100
    def pct(q: float) -> float:
        idx = min(len(ordered) - 1, max(0, int(round(q / 100 * (len(ordered) - 1)))))
        return ordered[idx]
    return pct(lo_q), pct(hi_q)


# ---------------- Clopper-Pearson 精确区间(零事件用) ----------------
# 为什么自己写而不用 scipy:pyproject 里没有声明 scipy(它是别的包带进来的间接依赖),
# 不能因为"本机恰好装了"就在评测脚本里 import 它 —— 换台机器跑就 ImportError。
# 下面的正则化不完全双函数是标准连分式实现(纯标准库,约 40 行)。


def _betacf(x: float, a: float, b: float) -> float:
    """不完全双函数的连分式(Lentz 法)。"""
    MAXIT, EPS, FPMIN = 300, 3e-15, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betai(x: float, a: float, b: float) -> float:
    """正则化不完全双函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(x, a, b) / a
    return 1.0 - bt * _betacf(1.0 - x, b, a) / b


def _beta_ppf(p: float, a: float, b: float) -> float:
    """Beta(a,b) 的 p 分位点(二分反解,单调所以安全)。"""
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _betai(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int) -> tuple[float, float]:
    """k/n 的 95% 双侧 Clopper-Pearson 精确区间。

    k=0 时下界恒为 0、上界为 1-(alpha/2)^(1/n)(即 Beta(1,n) 的 97.5% 分位);
    k=n 时对称。零事件正是本函数存在的理由:此时 bootstrap 会给出 [0,0] 的假窄区间。
    """
    if n <= 0:
        return 0.0, 1.0
    alpha = 1 - CI_LEVEL
    lo = 0.0 if k == 0 else _beta_ppf(alpha / 2, k, n - k + 1)
    hi = 1.0 if k >= n else _beta_ppf(1 - alpha / 2, k + 1, n - k)
    return lo, hi


def bootstrap(
    n: int,
    stat: Callable[[list[int]], float],
    *,
    rng: random.Random,
    n_boot: int = N_BOOT,
) -> tuple[float, float, float, int]:
    """对 0..n-1 索引有放回重采样;stat 返回 NaN(分母 0)的轮次不计。

    返回 (点估计, lo, hi, 有效轮)。**不含零事件特例** —— 那件事交给
    :func:`metric_ci`,因为零事件的分母是「该指标自己的子样本数」,不是这里的 n。
    """
    point = stat(list(range(n)))
    samples: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        value = stat(idx)
        if value == value:  # NaN 检测
            samples.append(value)
    lo, hi = percentile_ci(samples)
    return point, lo, hi, len(samples)


def metric_ci(
    *,
    n_event: int,
    n_denom: int,
    point: float,
) -> tuple[float, float, float, int, str]:
    """算比例类指标的 95% 区间 —— 一律用 Clopper-Pearson 精确法。

    ## 为什么整个文件的比例指标都换成 CP（2026-11-19）

    原来全部用百分位 bootstrap。它在两种情况下**会说谎**：
    - **零事件**（0/47 误拒）：2000 次重采样的分位点全落 0 → 报 `[0%, 0%]`，
      等于宣称「真实率必然是 0」
    - **全事件 / 极端比例**（dev 判定准确率 33/34 = 97%）：重采样的上端点反复顶到
      100% → 报 `[91%, 100%]`，其中 100% 这个端点没有统计含义

    同一张表里混用两种方法（误拒率 CP、准确率 bootstrap）还会让答辩追问
    「为什么口径不一致」。所以**凡是「k 次成功 / n 次试验」的比例指标，
    统一用 CP 精确区间**——它是二项分布的标准精确解，不依赖重采样，
    零事件和全事件都有意义（0/47 → [0%, 7.5%]，33/34 → [85%, 100%]）。

    bootstrap 只保留给**非二项指标**：macro-F1、MRR（这些不是「k/n」形态，
    CP 不适用）。

    返回 (点估计, lo, hi, 有效轮, 方法)。有效轮对 CP 没有意义（不重采样），
    但保留列位以便与 bootstrap 行对齐；这里填 n_denom 表示「参与该比例的样本数」。
    """
    if n_denom <= 0:
        return float("nan"), float("nan"), float("nan"), 0, "—"
    lo, hi = clopper_pearson(n_event, n_denom)
    return point, lo, hi, n_denom, "CP 精确"


def _fmt(point: float, lo: float, hi: float, valid: int, method: str = "bootstrap") -> list[str]:
    # 分母为 0(该 split 里没有这类样本)→ 该指标不可估,诚实写 N/A
    if point != point or lo != lo or hi != hi:
        return ["N/A（无该类样本）", "N/A", "0", "—"]
    return [
        f"{point:.1%}",
        f"[{lo:.1%}, {hi:.1%}]",
        str(valid),
        method,
    ]


# ---------------- G1/G2 判定指标 ----------------


def g12_metrics(
    labels_path: Path, preds_path: Path, split: str, *, ok_only: bool = False
) -> list[list[str]]:
    labels = load_rows(labels_path)
    preds = {row["rel_path"]: row for row in load_rows(preds_path)}
    scope = [row for row in labels if split == "all" or row.get("split") == split]

    def _is_g0(pred: dict[str, str]) -> bool:
        try:
            return json.loads(pred.get("raw_json") or "{}").get("stage") == "G0"
        except (json.JSONDecodeError, TypeError):
            return False

    rows = [
        (lab, preds[lab["rel_path"]])
        for lab in scope
        if lab["rel_path"] in preds
        and (lab.get("human_verdict") or "").strip()
        and (lab.get("human_quality") or "").strip()
        and not (ok_only and _is_g0(preds[lab["rel_path"]]))
    ]
    if not rows:
        raise SystemExit(f"[error] split={split} 下没有可计算的配对样本")

    def pf(pred: dict[str, str], key: str) -> str:
        return (pred.get(key) or "").strip()

    def make_stat(name: str) -> Callable[[list[int]], float]:
        def stat(idx: list[int]) -> float:
            sub = [rows[i] for i in idx]
            good = [(lab, pred) for lab, pred in sub
                    if lab["human_verdict"] in ("accept", "partial")]
            topic = [(lab, pred) for lab, pred in sub
                     if (lab.get("human_next_action") or "").strip() == "topic_dialog"]
            if name == "false_reject":
                if not good:
                    return float("nan")
                return sum(1 for lab, pred in good if pf(pred, "pred_verdict") == "reject") / len(good)
            if name == "false_drop":
                if not topic:
                    return float("nan")
                return sum(1 for lab, pred in topic if pf(pred, "pred_action") == "drop") / len(topic)
            if name == "topic_recall":
                if not topic:
                    return float("nan")
                return sum(1 for lab, pred in topic if pf(pred, "pred_action") == "topic_dialog") / len(topic)
            if name == "accuracy":
                return sum(1 for lab, pred in sub
                           if lab["human_verdict"] == pf(pred, "pred_verdict")) / len(sub)
            if name == "macro_f1":
                seen = ({lab["human_verdict"] for lab, _ in sub}
                        | {pf(pred, "pred_verdict") for _, pred in sub})
                f1s = []
                for cls in VERDICTS:
                    if cls not in seen:
                        continue
                    tp = sum(1 for lab, pred in sub
                             if lab["human_verdict"] == cls and pf(pred, "pred_verdict") == cls)
                    fp = sum(1 for lab, pred in sub
                             if lab["human_verdict"] != cls and pf(pred, "pred_verdict") == cls)
                    fn = sum(1 for lab, pred in sub
                             if lab["human_verdict"] == cls and pf(pred, "pred_verdict") != cls)
                    precision = tp / (tp + fp) if tp + fp else 0.0
                    recall = tp / (tp + fn) if tp + fn else 0.0
                    f1s.append(2 * precision * recall / (precision + recall)
                               if precision + recall else 0.0)
                return sum(f1s) / len(f1s) if f1s else float("nan")
            raise ValueError(name)  # noqa: TRY003 —— 内部调用,枚举受控

        return stat

    names = ["false_reject", "false_drop", "topic_recall", "accuracy", "macro_f1"]
    titles = {
        "false_reject": "误拒率(越低越好)",
        "false_drop": "误丢率(越低越好)",
        "topic_recall": "话题召回率",
        "accuracy": "判定准确率",
        "macro_f1": "macro-F1",
    }

    # 每个指标「是否零事件」要用**该指标自己的分母**判断(误拒的分母是该收样本、
    # 误丢的分母是 topic_dialog 样本),不能用总样本数。
    def good_rows() -> list[tuple[dict[str, str], dict[str, str]]]:
        return [(lab, pred) for lab, pred in rows
                if lab["human_verdict"] in ("accept", "partial")]

    def topic_rows() -> list[tuple[dict[str, str], dict[str, str]]]:
        return [(lab, pred) for lab, pred in rows
                if (lab.get("human_next_action") or "").strip() == "topic_dialog"]

    def event_count_for(name: str) -> tuple[int, int] | None:
        """返回 (该指标分母样本数, 分子事件数)。None = 连续指标(macro-F1),走 bootstrap。"""
        if name == "false_reject":
            g = good_rows()
            return len(g), sum(1 for _lab, pred in g if pf(pred, "pred_verdict") == "reject")
        if name == "false_drop":
            t = topic_rows()
            return len(t), sum(1 for _lab, pred in t if pf(pred, "pred_action") == "drop")
        if name == "topic_recall":
            t = topic_rows()
            return len(t), sum(1 for _lab, pred in t
                              if pf(pred, "pred_action") == "topic_dialog")
        if name == "accuracy":
            return len(rows), sum(1 for lab, pred in rows
                                  if lab["human_verdict"] == pf(pred, "pred_verdict"))
        return None  # macro_f1 是连续值,CP 不适用

    header = ["指标", "点估计", f"{int(CI_LEVEL * 100)}% 置信区间", "n", "区间方法"]
    out: list[list[str]] = []
    for name in names:
        rng = random.Random(SEED)  # 每个指标同一 seed,轮次对齐(可复现)
        stat = make_stat(name)
        point = stat(list(range(len(rows))))
        counts = event_count_for(name)
        if counts is None:
            _, lo, hi, valid = bootstrap(len(rows), stat, rng=rng)
            method = "bootstrap"
            n_shown = valid
        else:
            n_denom, n_event = counts
            point, lo, hi, n_shown, method = metric_ci(
                n_event=n_event, n_denom=n_denom, point=point,
            )
        out.append([titles[name], *_fmt(point, lo, hi, n_shown, method)])
    return [header, *out]


# ---------------- 检索指标 ----------------


def retrieval_metrics(per_query_path: Path) -> list[list[str]]:
    """per_query JSON → 检索指标 CI。

    ⚠️ per_query 字典里没有 is_negative 字段,负例靠 ``kind == "negative"`` 识别
    (q19/q20);它们的 hit_rank 恒为 None,**必须从分母里排除**,否则 R@5 被负例稀释。
    正例 hit_rank=None = 「没命中」,必须计入分母(这就是召回率的含义)。
    """
    data = json.loads(per_query_path.read_text(encoding="utf-8"))
    per_query = data["per_query"] if isinstance(data, dict) else data
    ranks = [
        q.get("hit_rank")
        for q in per_query
        if (q.get("kind") or "") != "negative"
    ]
    n = len(ranks)
    if n == 0:
        raise SystemExit("[error] per_query 里没有正例 query")

    def make_stat(name: str) -> Callable[[list[int]], float]:
        def stat(idx: list[int]) -> float:
            sub = [ranks[i] for i in idx]
            if name.startswith("recall@"):
                k = int(name.split("@")[1])
                return sum(1 for r in sub if r is not None and r <= k) / n
            if name == "mrr@10":
                return sum(1.0 / r for r in sub if r is not None and r <= 10) / n
            return sum(1.0 / r for r in sub if r is not None) / n

        return stat

    specs = [
        ("recall@5", "Recall@5"),
        ("recall@10", "Recall@10"),
        ("mrr@10", "MRR@10"),
        ("mrr", "MRR(全量)"),
    ]
    header = [
        f"指标(n={n} 条正例 query)", "点估计", f"{int(CI_LEVEL * 100)}% 置信区间",
        "n", "区间方法",
    ]
    out: list[list[str]] = []
    for name, title in specs:
        rng = random.Random(SEED)  # 每个指标同一 seed,轮次对齐
        stat = make_stat(name)
        point = stat(list(range(n)))
        if name.startswith("recall@"):
            # 召回率是「k 条命中 / n 条 query」的比例 → 与判定指标同口径用 CP
            k = int(name.split("@")[1])
            events = sum(1 for r in ranks if r is not None and r <= k)
            point, lo, hi, n_shown, method = metric_ci(
                n_event=events, n_denom=n, point=point,
            )
        else:
            # MRR 不是 k/n 形态,CP 不适用 → 保留 bootstrap
            _, lo, hi, n_shown = bootstrap(n, stat, rng=rng)
            method = "bootstrap"
        out.append([title, *_fmt(point, lo, hi, n_shown, method)])
    return [header, *out]


# ---------------- 输出 ----------------


def render(title: str, intro: str, table: list[list[str]]) -> str:
    head, *body = table
    lines = [f"# {title}", "", intro, "",
             f"方法:**比例类指标(误拒率/误丢率/话题召回率/判定准确率/Recall@k)"
             f"用 Clopper-Pearson 精确区间**(二项分布反解,95% 双侧)——零事件与"
             f"全事件都有意义;macro-F1 / MRR 这类连续指标用 B={N_BOOT} 次有放回"
             f"重采样,seed={SEED} 可复现,95% 百分位区间。", "",
             "⚠️ 为什么比例指标不用 bootstrap:**零事件时百分位法会报 `[0%, 0%]`**"
             "(0/47 重采样几乎每次都是 0 次事件,分位点全落端点),等于宣称"
             "「真实率必然是 0」——那是不成立的。CP 精确法给出 [0%, 7.5%],才是诚实口径。"
             "**例外**:MRR / macro-F1 不是「k/n 次数」形态,CP 不适用,仍用 bootstrap;"
             "它们若出现 `[0%, 0%]`(如全部 miss 时 MRR)是该指标的取值下界,不是口径错误。",
             "",
             "| " + " | ".join(head) + " |",
             "|" + "|".join(["---"] * len(head)) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in body]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bootstrap 置信区间(评测指标)")
    parser.add_argument("--labels", default=str(EVAL_DIR / "inputs" / "labels.csv"))
    parser.add_argument("--preds", default=str(EVAL_DIR / "runs" / "preds.csv"))
    parser.add_argument("--per-query", default=None,
                        help="ablation.py --json 的输出;给了就算检索指标 CI")
    parser.add_argument("--split", choices=("test", "dev", "all"), default="test")
    parser.add_argument(
        "--ok-only",
        action="store_true",
        help=(
            "ok 口径：剔除 G0 解析失败的样本（preds 的 raw_json 里 stage=G0）再算区间。"
            "与 metrics.py --ok-only 同口径，两个脚本的报告才能对照。"
        ),
    )
    parser.add_argument("--out", default=None, help="写 markdown 文件(不写则打 stdout)")
    args = parser.parse_args(argv)

    blocks: list[str] = []
    if args.per_query:
        table = retrieval_metrics(Path(args.per_query))
        blocks.append(render(
            "检索指标 bootstrap 置信区间",
            "数据:`ablation.py --json` 的 per_query 命中名次(真链路,300 档切块语料)。",
            table,
        ))
    else:
        table = g12_metrics(
            Path(args.labels), Path(args.preds), args.split, ok_only=args.ok_only
        )
        scope_note = f"split={args.split}" + ("，ok 口径（剔除 G0）" if args.ok_only else "")
        blocks.append(render(
            f"G1/G2 判定指标 bootstrap 置信区间({scope_note})",
            "数据:labels.csv(人工金标准)× preds.csv(线 J 预测)的配对样本。",
            table,
        ))

    text = "\n\n".join(blocks)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[ok] → {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
