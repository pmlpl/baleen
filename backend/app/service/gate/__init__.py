"""闸门层:G1 价值闸(启发式,零成本) 与 G2 归属闸(LLM,可降级)。

G1 只拦明确垃圾;G2 判「有没有可复用知识」并给出 action。
"""

from .g1 import GateResult, judge
from .g2 import G2Output, G2Result, PromptBundle, build_messages
from .g2 import judge as judge_g2

__all__ = [
    "G2Output",
    "G2Result",
    "GateResult",
    "PromptBundle",
    "build_messages",
    "judge",
    "judge_g2",
]
