"""切块参数(ADR 0004 §3)。

⚠️ 推导值,待 ADR 0004 §8 的消融实验校准(前置依赖:20 个 query 集)。
当前先按推导值实现并写成常量,便于实验时一处改。
"""

# 子块目标汉字数
TARGET_CHARS = 300
# 低于此值优先并到相邻块(除非是独立表格/代码)
MIN_CHARS = 250
# 硬上限:任何子块超过必须再切
MAX_CHARS = 400
# bge tokenizer 实测 token 硬上限(留 32 token 余量给 512 的 max_seq_length)
HARD_TOKEN_LIMIT = 480
# bge-large-zh-v1.5 的 max_seq_length(超过即被静默截断,用于算 chunks_truncated)
MODEL_MAX_TOKENS = 512
# 不做重叠(理由见 ADR §3:父块整节喂 LLM 已消化跨块断裂)
OVERLAP = 0

# 句子边界:在这些字符之后断句,绝不劈开句子
SENTENCE_BOUNDARIES = "。！？；\n"
# 目录页判定(ADR §4.5,命中 ≥2 条即判为目录)
TOC_SHORT_LINE_CHARS = 20
TOC_SHORT_LINE_RATIO = 0.6
TOC_AVG_LINE_CHARS = 20
