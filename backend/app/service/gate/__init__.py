"""闸门层:G1 价值闸(启发式)。G2 归属闸(LLM)后续任务。"""

from .g1 import GateResult, judge

__all__ = ["GateResult", "judge"]
