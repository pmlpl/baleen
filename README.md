# baleen

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

个人智能笔记库：**导入 → 判定 → 入知识库 → 可被追问并给出出处。**

> 名字取自鲸须（baleen）——鲸鱼靠它过滤海水、留下食物。
> 这个项目做的就是同一件事：过滤掉没有价值的内容，留下知识。

## ✨ 这是什么

baleen 解决的是"什么都往里塞、最后找不回来"的笔记问题。

和 Obsidian、Notion 这类「你自己负责整理」的工具不同，baleen 在**导入时**就用三道闸替你过滤——G0 解析（能不能读）、G1 价值（有没有实质内容）、G2 归属（属不属于这个知识库），没价值或不相关的进不来。收进来的每一条都能被追问，且回答**带笔记出处**、不瞎编；库内答不了时兜底联网或用 AI 自身知识二次回答，并标注来源。

除了文档，还能把 **B 站 / 抖音视频直接转成带配图的笔记**（whisper 转写 + 场景抽帧 + 视觉逐字转录）。本地部署、多用户隔离、每个账号自带 LLM 配置。

## 🔄 核心流程

```
文件导入（pdf / docx / pptx / md / txt / csv / 图片 / 扫描件 PDF / 视频：B站·抖音）
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

## 🧩 已实现的功能

- **多格式文档导入**：PDF（含扫描件逐页视觉识别）、DOCX、PPTX、Markdown（带本地图片）、TXT、CSV、图片（视觉转写）、HTML；`.doc`/`.xlsx`/`.xls` 显式拒绝并给转换提示
- **视频→笔记**：B 站 / 抖音链接导入，faster-whisper 异步转写 + PySceneDetect 场景抽帧 + 视觉逐字转录 + 真嵌图，导入全程白盒进度日志
- **三道准入闸**：G0 解析 / G1 价值 / G2 归属，便宜的先跑早退
- **混合检索**：pgvector 向量 + pg_trgm 全文 + RRF 融合重排，small-to-big 喂给 LLM
- **问答主界面**：SSE 流式回答、引用笔记出处、会话进 URL、`[NO_KB]` 兜底不拒答、斜杠命令、灯箱放大、📎 对话附件（大文件分层分析）、长对话分段摘要（保前缀缓存）
- **笔记自编辑**：PUT 改标题/正文（含图笔记可编辑，图片位置标记往返）、DELETE 真删除（DB 级联）
- **图片视觉理解**：导入时视觉模型转写进流水线、问答回传原图、SVG 插图沙箱渲染
- **输出格式**：markdown 表格、嵌套列表、mermaid 思维导图/流程图/统计图（懒加载）、SVG 矢量图、KaTeX 数学公式（行内/块级）
- **多用户隔离**：用户名+密码+JWT，数据按 `owner_id` 隔离，每用户独立 LLM 配置
- **章程问卷**：6 步向导生成自然语言 charter，驱动 G2 判定
- **评测**：G1/G2 真链路评测 + bootstrap 95% CI、检索 R@5 评测、真实导入候选样本自动归档

## 🛠 技术栈

| 层 | 选定 |
|---|---|
| 后端 | Python 3.13 / FastAPI / SQLAlchemy 2 async / arq + Redis |
| 数据库 | PostgreSQL 17 + pgvector（HNSW）+ pg_trgm |
| 向量 | 本地 `bge-large-zh-v1.5`（1024 维，GPU 加速） |
| 前端 | Vue 3 + TypeScript / Vite / Pinia / TipTap |
| LLM | OpenAI 兼容协议（DeepSeek 等），每用户独立配置 |
| 视频 | faster-whisper（small，GPU）+ PySceneDetect + Playwright（抖音免 cookie / B 站兜底） |
| 基础设施 | Docker Compose（PostgreSQL + Redis） |

## 📁 目录

| 目录 | 内容 |
|---|---|
| `backend/` | Python / FastAPI（API + arq worker） |
| `frontend/` | Vue 3 + TypeScript |
| `docs/` | `api.md`（前后端接口契约）+ `adr/`（技术决策记录） |
| `eval/` | 评测脚本 + 标注 + 报告（含 bootstrap CI） |

## 🚀 快速开始

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

## 📜 文档索引

完整文档导航见 [NAVIGATION.md](NAVIGATION.md)（点击跳转）。本处只列开工必读：

- [AGENTS.md](AGENTS.md) —— 项目约定，任何 AI 编码代理开工前必读
- [STATUS.md](STATUS.md) —— 系统状态速览（模块表 / 基线 / 已知限制）

## 🧪 测试

```powershell
cd backend
# 需 Docker 容器运行（docker compose up -d）
uv run pytest
```

## 📐 文档一致性

文档只写「现在是什么」，历史交付块按阶段归档在 `backend/delivery/`。
**一致性不靠人记，靠一条命令**：

```powershell
python scripts/doc_lint.py         # ERROR 必须为 0
python scripts/doc_backfill.py --since 2026-11-01   # 漏写的交付块，生成骨架补
```

`pre-commit` 已挂这个闸（`git config core.hooksPath .githooks`，换机器需重装一次）：有 ERROR 提交不进去。

## 📄 License

MIT
