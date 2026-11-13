# baleen

个人智能笔记库：**导入 → 判定 → 入知识库 → 可被追问并给出出处。**

> 名字取自鲸须（baleen）——鲸鱼靠它过滤海水、留下食物。
> 这个项目做的就是同一件事：过滤掉没有价值的内容，留下知识。

## 现在是什么状态

**状态只在两处维护，本 README 不重复数字：**

- 工程状态（模块表 / 测试基线 / 已知限制）→ `STATUS.md` 顶部「系统状态速览」
- 时间线（哪天做了什么、为什么）→ `../日志/YYYY-MM-DD.md`

最近一次收口：2026-11-13，whisper 异步化 v23，基线 **434 passed**。
架构决策见 `docs/adr/0001`–`0009`，接口契约见 `docs/api.md`（v23）。

**这是本科毕业设计**，目标是本地多用户可用的完整闭环，不是多用户 SaaS。

## 核心流程

```
文件导入（pdf / docx / pptx / md / txt / csv / 图片 / 扫描件 PDF）
  ↓
G0 解析闸 ── 能不能读出东西？（确定性规则，零成本）
  ↓
G1 价值闸 ── 有没有实质内容？（启发式，近零成本）
  ↓
G2 归属闸 ── 属不属于这个知识库、有没有可复用知识？（一次文件级 LLM 调用）
  ↓
accept / partial / reject  ── reject 可申诉，partial 只抽有价值部分
  ↓
切块（≤400 汉字）→ 向量化（本地 bge-large-zh-v1.5）→ 混合检索（向量 + 全文 + RRF 重排）
  ↓
追问 + 带出处的回答（SSE 流式，[NO_KB] 时兜底联网/AI 自身知识）
```

## 已实现的功能

- **8 种格式导入**：PDF（含扫描件逐页视觉识别）、DOCX、PPTX、Markdown（带本地图片）、TXT、CSV、图片（视觉转写）；`.doc`/`.xlsx`/`.xls` 显式拒绝并给转换提示
- **三道准入闸**：G0 解析 / G1 价值 / G2 归属，便宜的先跑早退
- **混合检索**：pgvector 向量 + pg_trgm 全文 + RRF 融合重排，small-to-big 喂给 LLM
- **问答主界面**：SSE 流式回答、引用笔记出处、会话进 URL、`[NO_KB]` 兜底不拒答、斜杠命令、灯箱放大
- **笔记自编辑**：PUT 改标题/正文（含图笔记可编辑，图片位置标记往返）、DELETE 真删除（DB 级联）
- **图片视觉理解**：导入时视觉模型转写进流水线、问答回传原图、SVG 插图沙箱渲染
- **输出格式**：markdown 表格、嵌套列表、mermaid 思维导图/流程图/统计图（懒加载）、SVG 矢量图
- **多用户隔离**：用户名+密码+JWT，数据按 `owner_id` 隔离，每用户独立 LLM 配置
- **章程问卷**：6 步向导生成自然语言 charter，驱动 G2 判定
- **评测**：G1/G2 真链路评测 + bootstrap 95% CI、检索 R@5 评测、真实导入候选样本自动归档

## 技术栈

| 层 | 选定 |
|---|---|
| 后端 | Python 3.13 / FastAPI / SQLAlchemy 2 async / arq + Redis |
| 数据库 | PostgreSQL 17 + pgvector（HNSW）+ pg_trgm |
| 向量 | 本地 `bge-large-zh-v1.5`（1024 维，GPU 加速） |
| 前端 | Vue 3 + TypeScript / Vite / Pinia / TipTap |
| LLM | OpenAI 兼容协议（DeepSeek 等），每用户独立配置 |
| 基础设施 | Docker Compose（PostgreSQL + Redis） |

## 目录

| 目录 | 内容 |
|---|---|
| `backend/` | Python / FastAPI（API + arq worker） |
| `frontend/` | Vue 3 + TypeScript |
| `docs/` | `api.md`（前后端契约 v18）+ `adr/`（0001–0006 技术决策记录） |
| `eval/` | 评测脚本 + 标注 + 报告（含 bootstrap CI） |

## 快速开始

**一键启动**（Windows，推荐）：

```powershell
# 自动起 Docker(db+redis) → worker → API → 前端，按序等待健康检查
start.bat
```

**手动启动**：

```powershell
# 1. 起依赖服务（PostgreSQL + pgvector、Redis）
docker compose up -d

# 2. 后端 API
cd backend
uv sync
uv run alembic upgrade head     # 数据库迁移
uv run fastapi dev               # http://127.0.0.1:8000/docs

# 3. worker（导入流水线，另开终端）
cd backend
uv run arq app.worker.worker.WorkerSettings

# 4. 前端
cd frontend
pnpm install
pnpm dev                          # http://localhost:5173
```

> 首次导入较慢：bge 嵌入模型冷启动加载，后端启动时后台预热。
> `.env` 配置见 `.env.example`（LLM 网关 / API Key / Tavily / 嵌入模型路径）。
> 新注册账号需到设置页配置自己的 LLM API Key 才能导入。

## 文档索引

完整文档导航见 [NAVIGATION.md](NAVIGATION.md)（点击跳转）。本处只列开工必读：

- [AGENTS.md](AGENTS.md) —— 项目约定，任何 AI 编码代理开工前必读
- [STATUS.md](STATUS.md) —— 系统状态速览（模块表 / 基线 / 已知限制）

## 测试

```powershell
cd backend
# 需 Docker 容器运行（docker compose up -d）
uv run pytest
# 基线 434 passed
```

## 文档一致性

文档只写「现在是什么」，历史交付块按阶段归档在 `backend/delivery/`。
**一致性不靠人记，靠一条命令**：

```powershell
python scripts/doc_lint.py         # ERROR 必须为 0
python scripts/doc_backfill.py --since 2026-11-01   # 漏写的交付块，生成骨架补
```

`pre-commit` 已挂这个闸（`git config core.hooksPath .githooks`，换机器需重装一次）：有 ERROR 提交不进去。
