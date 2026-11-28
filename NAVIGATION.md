# 导航

> 给主人用的文档入口，点击即跳。文档索引只此一处；各文档自身不重复列邻居。
> 路径均相对于仓库根 `baleen/`；`../日志/` 在仓库外（工作目录下）。

## 入口与状态

- [README](README.md) — 项目门面（技术栈 / 快速开始 / 给外部读者）
- [系统状态速览 STATUS](STATUS.md) — 模块表 / 测试基线 / 已知限制 / 未决（★ 迷路先读这个）
- [AGENTS](AGENTS.md) — AI 代理约定，开工必读
- [HANDOFF](HANDOFF.md) — 线间交接台（多 agent 协调）
- [黑话词典](docs/glossary.md) — 项目术语速查（G0/organizer/父块/基线/交付块……看不懂的词先查这里）

## 契约与决策

- [API 契约 v45](docs/api.md) — 前后端接口（版本以「变更记录」为准）
- ADR（技术决策，答辩素材）：
  - [0001 架构基线](docs/adr/0001-architecture-baseline.md)
  - [0002 解析栈](docs/adr/0002-parsing-stack.md)
  - [0003 数据模型](docs/adr/0003-data-model.md)
  - [0004 文本切分](docs/adr/0004-text-splitting.md)
  - [0005 导入摘要](docs/adr/0005-import-summary-and-artifacts.md)
  - [0006 图片视觉](docs/adr/0006-image-vision.md)
  - [0007 B 站字幕导入](docs/adr/0007-bilibili-subtitle-import.md)
  - [0008 G2 变编辑](docs/adr/0008-g2-judge-to-editor.md)
  - [0009 视频异步提取](docs/adr/0009-async-video-extraction.md)
  - [0010 问答链 agent 自主权边界](docs/adr/0010-qa-agent-harness.md)
- [笔记格式规范](docs/note-format.md) — 完整笔记契约（格式层+内容层），人/AI 写笔记前必读；charter 由问卷生成存 DB，不再独立成文件

## 交付历史

- 后端归档：[foundation](backend/delivery/2026-10-foundation.md) · [gates-retrieval](backend/delivery/2026-10-gates-retrieval.md) · [qa-and-eval](backend/delivery/2026-10-qa-and-eval.md) · [2026-11](backend/delivery/2026-11.md)
- [评测线交付](eval/DELIVERY.md) · [前端线交付](frontend/DELIVERY.md)

## 评测（自己跑的说明书：[eval/README](eval/README.md)——一条规则 + 6 步 + 该看到的数字）

- 任务与规则：[TASK 任务书](eval/TASK.md) · [rubric 评分规则](eval/rubric.md) · [复核单](eval/queries/复核单.md)
- 跑批产物（`runs\` 可删可重跑，跑一下就回来）：preds / report / ci / negative_report——生成与看法见 [eval/README](eval/README.md)
- 消融：[ablation](eval/analysis/ablation.md) · [prefix_ablation](eval/analysis/prefix_ablation.md)
- 缺陷台账（模式归类 + A 档回归缺口）：[defects_ledger.md](eval/analysis/defects_ledger.md) · [defects_ledger.csv](eval/analysis/defects_ledger.csv)
- 负例题集：[negative_queries.csv](eval/queries/negative_queries.csv) · [q10 复核单](eval/queries/q10_复核单.md)

## 时间线（仓库外）

- [日志/](../日志/) — 按天开发日志 · [日志约定 README](../日志/README.md)

## 维护工具（脚本，非文档）

- `scripts/doc_lint.py` — 文档一致性检查（交付前必跑，0 ERROR）
- `scripts/doc_backfill.py` — 从 git log 生成待填交付块骨架
