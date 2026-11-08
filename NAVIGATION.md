# 导航

> 给主人用的文档入口，点击即跳。文档索引只此一处；各文档自身不重复列邻居。
> 路径均相对于仓库根 `baleen/`；`../日志/` 在仓库外（工作目录下）。

## 入口与状态

- [README](README.md) — 项目门面（技术栈 / 快速开始 / 给外部读者）
- [系统状态速览 STATUS](STATUS.md) — 模块表 / 测试基线 / 已知限制 / 未决（★ 迷路先读这个）
- [AGENTS](AGENTS.md) — AI 代理约定，开工必读
- [HANDOFF](HANDOFF.md) — 线间交接台（多 agent 协调）

## 契约与决策

- [API 契约 v18](docs/api.md) — 前后端接口（版本以「变更记录」为准）
- ADR（技术决策，答辩素材）：
  - [0001 架构基线](docs/adr/0001-architecture-baseline.md)
  - [0002 解析栈](docs/adr/0002-parsing-stack.md)
  - [0003 数据模型](docs/adr/0003-data-model.md)
  - [0004 文本切分](docs/adr/0004-text-splitting.md)
  - [0005 导入摘要](docs/adr/0005-import-summary-and-artifacts.md)
  - [0006 图片视觉](docs/adr/0006-image-vision.md)
- [charter 章程](charter.md) — G2 归属闸的运行时输入（非常规文档，清理时别碰）

## 交付历史

- 后端归档：[foundation](backend/delivery/2026-10-foundation.md) · [gates-retrieval](backend/delivery/2026-10-gates-retrieval.md) · [qa-and-eval](backend/delivery/2026-10-qa-and-eval.md) · [pending 待填](backend/delivery/pending.md)
- [评测线交付](eval/DELIVERY.md) · [前端线交付](frontend/DELIVERY.md)

## 评测

- 任务与规则：[TASK 任务书](eval/TASK.md) · [rubric 评分规则](eval/rubric.md) · [复核单](eval/queries/复核单.md) · [queries/README](eval/queries/README.md)
- 报告（三口径）：[report · test 18](eval/report.md) · [report_all · 全量 35](eval/report_all.md) · [report_g2_ok · ok 21](eval/report_g2_ok.md)
- 消融：[ablation](eval/ablation.md) · [prefix_ablation](eval/prefix_ablation.md)
- 置信区间：[ci_g12_dev](eval/ci_g12_dev.md) · [ci_g12_test](eval/ci_g12_test.md) · [ci_retrieval](eval/ci_retrieval.md)

## 时间线（仓库外）

- [日志/](../日志/) — 按天开发日志 · [日志约定 README](../日志/README.md)

## 维护工具（脚本，非文档）

- `scripts/doc_lint.py` — 文档一致性检查（交付前必跑，0 ERROR）
- `scripts/doc_backfill.py` — 从 git log 生成待填交付块骨架
