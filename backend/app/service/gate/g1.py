"""G1 价值闸(纯启发式、零成本、不联网、不调 LLM)。

拦「明确无价值」的垃圾;模棱两可的一律放过交给 G2。
⚠️ AGENTS.md 决策 8:单用户场景【误拒代价 ≫ 误收代价】——
   G1 只拦明确垃圾,绝不因"短/看不懂/没小标题"就 reject(英文论文、报错堆栈都放过)。

判定口径对齐 eval/rubric.md §2.1;输出字段对齐 review_runs 表。
阈值是推导值,待 eval/labels.csv 的 35 条人工标注校准。
"""

from __future__ import annotations

import re
from collections import Counter
from math import log2

from pydantic import BaseModel, Field

from app.service.parse.blocks import flatten_text
from app.service.parse.types import ParseResult

# ---- 阈值(推导值,待 eval/labels.csv 35 条人工标注校准)----
SHORT_CHARS = 30  # 去空白后 < 此值 → 直接 junk
# 样板词:只在短文里才算直接 junk(长文里出现「placeholder/占位」多是正文提及,不 junk)
BOILERPLATE_MAX_CHARS = 200
BOILERPLATE = re.compile(
    r"hello[\s,]*world|lorem\s+ipsum|这是一个测试|无标题|草稿"
    r"|test\s*file|this\s+is\s+a\s+test",
    re.IGNORECASE,
)
ENTROPY_MIN = 2.5  # 字符熵低于此 → 减分
TTR_MIN = 0.3  # 词型-词例比低于此 → 减分
SHINGLE_REPEAT_MAX = 0.7  # 自我重复率高于此 → 减分
REJECT_SCORE = 0.5  # 综合得分低于此 → reject
# 罚分(每个信号都要能单独解释)
PEN_ZERO_STRUCTURE = 0.4
PEN_LOW_ENTROPY = 0.2
PEN_LOW_TTR = 0.2
PEN_HIGH_REPEAT = 0.3
PEN_BOILERPLATE_SOFT = 0.2  # 长文里出现样板词(正文提及)仅减分,不当 junk
# accept 档位(按得分标 core/normal/reference)
QUAL_CORE = 0.8
QUAL_NORMAL = 0.6
# 信息密度信号为避免长文本自然衰减,只取前 N 字符算
_TTR_WINDOW = 1000
_SHINGLE_WINDOW = 2000


class GateResult(BaseModel):
    """G1 判定结果,字段与 review_runs 表对齐(gate/verdict/quality/score/action/reasons)。"""

    gate: str = "G1"
    verdict: str  # accept | reject
    quality: str  # core | normal | reference | junk
    score: float
    action: str  # index | drop
    reasons: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


def _count_structure(blocks: dict) -> dict:
    """数 block 类型;zero_structure = 无标题/列表/表格/代码 且 段落 ≤ 1。"""
    counts = Counter()
    for b in blocks.get("content") or []:
        counts[b.get("type")] += 1
    zero = (
        counts.get("heading", 0) == 0
        and counts.get("bulletList", 0) + counts.get("orderedList", 0) == 0
        and counts.get("table", 0) == 0
        and counts.get("codeBlock", 0) == 0
        and counts.get("paragraph", 0) <= 1
    )
    return {**dict(counts), "zero_structure": zero}


def _char_entropy(text: str) -> float:
    if not text:
        return 0.0
    freq = Counter(text)
    n = len(text)
    return -sum((c / n) * log2(c / n) for c in freq.values() if c)


def _cjk_ttr(text: str) -> float:
    """词型-词例比,只在 CJK 字符上算(英文无 CJK → 1.0 不罚)。

    char-level TTR 对英文不公(字母表小、TTR 天然低),会误伤英文论文;
    中文没有词边界,char-level TTR 反而能反映重复度。与「中文占比只记录不判定」一致。
    """
    chars = [c for c in text[:_TTR_WINDOW] if "\u4e00" <= c <= "\u9fff"]
    return len(set(chars)) / len(chars) if chars else 1.0


def _shingle_repeat(text: str) -> float:
    chars = re.sub(r"\s+", "", text)[:_SHINGLE_WINDOW]
    if len(chars) < 10:
        return 0.0
    shingles = [chars[i : i + 5] for i in range(len(chars) - 4)]
    return 1 - len(set(shingles)) / len(shingles)


def judge(parse_result: ParseResult, charter: str | None = None) -> GateResult:  # noqa: ARG001
    """G1 判定。charter 预留(决策 8 的 charter「不收」清单,目前未启用,不参与判定)。"""
    blocks = parse_result.blocks
    text = flatten_text(blocks)
    stripped = re.sub(r"\s+", "", text)
    reasons: list[str] = []
    score = 1.0

    # 中文占比:只记录,不参与判定(避免误伤英文论文)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", stripped))
    cjk_ratio = cjk / len(stripped) if stripped else 0.0

    # ---- 直接 junk(任一命中即 reject/junk/drop)----
    direct_junk = False
    if len(stripped) < SHORT_CHARS:
        direct_junk = True
        reasons.append(f"极短:去空白后 {len(stripped)} 字符 < {SHORT_CHARS}")
    m = BOILERPLATE.search(text)
    if m:
        if len(stripped) < BOILERPLATE_MAX_CHARS:
            direct_junk = True
            reasons.append(f"样板词命中:{m.group(0)!r}(且全文 < {BOILERPLATE_MAX_CHARS} 字符)")
        else:
            score -= PEN_BOILERPLATE_SOFT
            reasons.append(f"正文出现样板词 {m.group(0)!r}(长文,仅减分)")
    if direct_junk:
        return GateResult(
            verdict="reject",
            quality="junk",
            score=0.0,
            action="drop",
            reasons=reasons,
            meta={"chars": len(stripped), "cjk_ratio": round(cjk_ratio, 3)},
        )

    # ---- 减分信号 ----
    struct = _count_structure(blocks)
    if struct["zero_structure"]:
        score -= PEN_ZERO_STRUCTURE
        reasons.append("零结构:无标题/列表/表格/代码且段落 ≤ 1")
    ent = _char_entropy(text)
    if ent < ENTROPY_MIN:
        score -= PEN_LOW_ENTROPY
        reasons.append(f"字符熵偏低:{ent:.2f} < {ENTROPY_MIN}")
    ttr = _cjk_ttr(text)
    if ttr < TTR_MIN:
        score -= PEN_LOW_TTR
        reasons.append(f"词型-词例比偏低(CJK):{ttr:.2f} < {TTR_MIN}")
    rep = _shingle_repeat(text)
    if rep > SHINGLE_REPEAT_MAX:
        score -= PEN_HIGH_REPEAT
        reasons.append(f"自我重复率高:{rep:.2f} > {SHINGLE_REPEAT_MAX}")
    score = max(score, 0.0)

    if score < REJECT_SCORE:
        reasons.append(f"综合得分 {score:.2f} < {REJECT_SCORE}")
        return GateResult(
            verdict="reject",
            quality="junk",
            score=score,
            action="drop",
            reasons=reasons,
            meta={
                "chars": len(stripped),
                "cjk_ratio": round(cjk_ratio, 3),
                "entropy": round(ent, 2),
                "ttr": round(ttr, 2),
                "repeat": round(rep, 2),
            },
        )

    # ---- accept:按得分标档 ----
    quality = "core" if score >= QUAL_CORE else "normal" if score >= QUAL_NORMAL else "reference"
    reasons.append(f"综合得分 {score:.2f} → {quality}")
    return GateResult(
        verdict="accept",
        quality=quality,
        score=score,
        action="index",
        reasons=reasons,
        meta={
            "chars": len(stripped),
            "cjk_ratio": round(cjk_ratio, 3),
            "entropy": round(ent, 2),
            "ttr": round(ttr, 2),
            "repeat": round(rep, 2),
            "structure": {k: v for k, v in struct.items() if k != "zero_structure"},
        },
    )
