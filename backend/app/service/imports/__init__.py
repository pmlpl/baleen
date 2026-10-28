"""导入状态机:文件 → sha256/去重 → blob → G0 → G1 → G2 → note → 切块 → 向量化 → 落库。

本包目录名用 ``imports`` 而不是 ``import`` —— ``import`` 是 Python 关键字,
``from app.service.import...`` 是语法错误,无法正常导入(HANDOFF §3 允许目录细分的命名自决)。

设计:
- 确定性状态机,不是 agent loop;每个阶段 commit,失败记 jobs.last_error,可重试/续跑;
- 幂等:同 sha256 且 jobs.status=done → 直接返回已有 source,不重复调 G2;
- 可重放:已落库的阶段产物(source.doc / review_runs / note / chunks)会被复用,不重复写;
- 对外暴露同步门面 ``run_import()``(CLI 可直接调)与异步核心 ``run_import_async()``(arq 可 await)。
"""

from .pipeline import ImportResult, run_import, run_import_async

__all__ = ["ImportResult", "run_import", "run_import_async"]
