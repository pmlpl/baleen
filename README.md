# baleen

中文优先的个人智能笔记库：**导入 → 判定 → 入知识库 → 可被追问并给出出处。**

> 名字取自鲸须（baleen）——鲸鱼靠它过滤海水、留下食物。
> 这个项目做的就是同一件事：过滤掉没有价值的内容，留下知识。

## 现在是什么状态

| 阶段 | 状态 |
|---|---|
| 架构设计 | ✅ 已定（见 `AGENTS.md` §5） |
| 评测脚手架 | 🚧 进行中（见 `eval/TASK.md`） |
| 后端 / 前端 | ⬜ 未开始 |

**这是本科毕业设计**，目标是单用户可用的完整闭环，不是多用户 SaaS。

## 核心流程

```
文件导入
  ↓
解析（pdf / docx / md / txt / html / 图片 OCR）
  ↓
G0 解析闸 ── 能不能读出东西？（确定性规则，零成本）
  ↓
G1 价值闸 ── 有没有实质内容？（启发式，近零成本）
  ↓
G2 归属闸 ── 属不属于这个知识库、有没有可复用知识？（一次文件级 LLM 调用）
  ↓
accept / partial / reject  ── reject 可申诉，partial 只抽有价值部分
  ↓
切块（≤400 汉字）→ 向量化（本地 bge-large-zh-v1.5）→ 混合检索
  ↓
追问 + 带出处的回答
```

## 目录

| 目录 | 内容 |
|---|---|
| `backend/` | Python / FastAPI |
| `frontend/` | Vue 3 + TypeScript |
| `docs/` | 架构、API 契约、ADR 决策记录 |
| `eval/` | 评测资产（脚本 + 标注 + 报告） |

## 快速开始

```powershell
# 起依赖服务（PostgreSQL + pgvector、Redis）
docker compose up -d

# 后端
cd backend; uv sync; uv run fastapi dev

# 前端
cd frontend; pnpm install; pnpm dev

# 评测（详细见 eval/TASK.md）
python eval/scripts/candidates.py
```

## 文档索引

- `AGENTS.md` —— **项目约定，任何 AI 编码代理开工前必读**
- `eval/TASK.md` —— 评测脚手架任务书
- `docs/architecture.md` —— 架构与数据模型
- `docs/api-contract.md` —— 前后端契约
- `docs/adr/` —— 技术决策记录（答辩素材）
