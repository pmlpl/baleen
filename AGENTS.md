# AGENTS.md — baleen 项目约定

> **任何 AI 编码代理（Trae / WorkBuddy / Claude Code / DSH / Cursor…）在动手前必须读完这份文件。**
> 这份文件是唯一的约定来源。里面写"已锁定"的东西，不要重新讨论、不要擅自替换。
>
> ⛔ **本文件只能由人类修改。任何 AI 代理不得创建、编辑、删除或"顺手更新"本文件。**
> 需要变更时，在回复里提出建议和 diff，由人类落笔。

---

## 1. 这是什么

**baleen** —— 中文优先的**个人**智能笔记库。

一句话定位：**一个能自己判断该不该收、收了之后还能被追问出处的本地知识库。**

- 交付背景：本科毕业设计（软件过程技术方向）
- 场景：**单用户**。暂不做注册登录、多租户、计费
- 核心价值不在"存笔记"，在**准入判定**和**带出处的检索**

---

## 2. 目录职责

| 目录 | 内容 | 谁可以改 |
|---|---|---|
| `backend/` | Python / FastAPI 后端 | 后端任务 |
| `frontend/` | Vue 3 + TS 前端 | 前端任务 |
| `docs/` | 架构、API 契约、ADR | 文档任务 |
| `docs/adr/` | 技术决策记录（**答辩素材，边做边记**） | 文档任务 |
| `eval/` | 评测脚本、标注、报告 | 评测任务 |
| `eval/samples/` | **真实样本文件** | ❌ **任何人都不许动** |

**一句话边界**：一次任务只碰一个顶层目录；要跨目录，先说明为什么。

---

## 3. 硬约束（违反即返工）

1. **一次任务只碰一个顶层目录**（`backend/` / `frontend/` / `docs/` / `eval/` 之一）。
2. **不许擅自新增第三方依赖**。需要时先给出：用途、体积、维护状态、替代方案。等确认。
3. **不许创建或修改 `.env`**。只能改 `.env.example`。
4. **不许改 `eval/samples/` 下任何文件** —— 包括重命名、移动、修改内容。**文件名本身就是评测特征**，改了就等于毁掉样本。
5. **不许把任何密钥、token、密码写进代码或提交历史。**
6. 数据表结构变更**必须**带 Alembic migration，不许手改数据库。
7. 改完必须能跑通：`docker compose up -d` → 后端 `GET /healthz` 返回 200。
8. **不许推翻第 4、5 节已锁定的技术栈与架构**。要换，先写 `docs/adr/` 提案，等人确认。
9. **不许提前实现未列入范围的功能**（见第 8 节"明确不做"）。
10. Python 侧统一用 `uv` 管理依赖，不许混用 `pip install` / `requirements.txt`。
11. **禁止 AI 修改 `AGENTS.md` 本身。** 这是 agent 自己的"宪法"，能改它等于能给自己扩权。
    需要变更就提建议，由人类落笔。人类改完请更新第 11 节变更记录。

---

## 4. 技术栈（已锁定）

### 后端
| 项 | 选定 | 备注 |
|---|---|---|
| 语言 | **Python 3.13.13** | 实测环境版本；若某库不兼容则降 3.12（`uv venv --python 3.12`） |
| 包管理 | **uv 0.12.2** | `uv sync` / `uv add` / `uv run` |
| 框架 | **FastAPI** + uvicorn | |
| ORM | **SQLAlchemy 2.x（async）** + **Alembic** | |
| 校验 | **Pydantic v2** | |
| 队列 | **arq** + Redis | 导入是长任务，**不许用 BackgroundTasks** |
| 数据库 | **PostgreSQL 17 + pgvector** | 业务 + 向量 + 全文 + JSONB 全在一个事务里 |
| 中文全文 | `pg_trgm` / `pg_bigm`（先 trgm） | 不用 zhparser（Windows 编译麻烦） |
| 对象存储 | 本地内容寻址目录 | `storage/{sha256[:2]}/{sha256}`，**blob 不许进数据库** |
| Embedding | **本地 `BAAI/bge-large-zh-v1.5`** | 见 §5 决策 4，路径在 §6 |
| 解析 | **pymupdf**(pdf) / **python-docx**(docx) / **markdown-it-py**(md) / **python-pptx**(pptx) / **`.txt` 纯文本直通** | 已定，见 `docs/adr/0002-parsing-stack.md`。`.txt` 编码探测 UTF-8→GB18030→`errors=replace`+warning。**`.doc` 不支持**——G0 明确报「请转成 .docx」；**扫描件与纯图片 → `no_text_expected`**（本来没文本层，该走 OCR，不是格式错误）；`.xlsx` 暂不支持 |
| 流式 | **SSE** | 不用 WebSocket |

### 前端
| 项 | 选定 |
|---|---|
| 框架 | **Vue 3 + TypeScript**（Composition API + `<script setup>`） |
| 构建 | **Vite** + **pnpm 11.22**（**不许用 npm**，Windows 长路径会炸） |
| 状态 | Pinia |
| 路由 | Vue Router |
| 编辑器（v2） | TipTap（ProseMirror 系，文档模型即 block JSON） |

### 基础设施
Docker 29.8.0 + Compose v5.5.1（`psql` 客户端未装，一律走容器）

---

## 5. 已定的架构决策（**不要重新讨论**）

| # | 决策 | 理由（一句话） |
|---|---|---|
| 1 | **Postgres 单库承载业务 + 向量 + 全文** | "删一篇笔记"是跨 5 张表的事务操作，双写必然产生孤儿数据 |
| 2 | **不引入独立向量库**（Qdrant/Milvus） | 按 1 万篇 / 2.5 万 chunk 设计，PG 连热身都算不上 |
| 3 | **笔记真源 = block JSON**（TipTap 兼容），markdown 只是渲染 | 表格/图片/公式/代码需要结构；markdown 只是视图 |
| 4 | **Embedding 用本地 bge-large-zh-v1.5** | 离线、免费、断网可演示；但**max_seq_length = 512 token**，见下方警告 |
| 5 | **导入是状态机，不是 agent loop** | 要可重放、可断点续跑、成本可控、能排查 |
| 6 | **审核拆三道闸**：G0 解析（确定性）/ G1 价值（启发式）/ G2 归属（LLM 一次/文件） | 便宜的先跑、早退，能挡掉 80–90% 的垃圾不进 LLM |
| 7 | **G2 判定对象是"有没有可复用知识"，不是"属于哪个学科"** | 领域分类永远列不全；用途才是有限集 |
| 8 | **charter 是每个知识库自己的一段自然语言**，不是枚举字段 | 代码里**永远不许出现** `domain ENUM(...)` |
| 9 | **单用户 → 闸门策略是"默认收 + 标质量"，不是"默认拒"** | 误拒代价 ≫ 误收代价（收错了能删，拒错了你不知道丢了什么） |
| 10 | **LLM 全部走 OpenAI 兼容协议 + adapter 表** | 用户自带 key，Ollama / vLLM / 各家 API 统一 |
| 11 | **embedding 表带 `model + dim`**，换模型走"重建 + 灰度切换" | 换 embedding = 全库重建，绝不原地覆盖 |
| 12 | 业务表预留 `owner_id`（单用户写死 1） | 以后给别人用只是"常量换变量"，不用重构 |

### ⚠️ 决策 4 的硬后果（最容易踩的坑）

`bge-large-zh-v1.5` 的 `max_seq_length = 512`，vocab 21128 → **约 1 汉字 ≈ 1 token**。

- **切块上限：250–400 汉字**。超过会被**静默截断**，召回掉点且极难排查。
- **小块检索 + 父块喂给 LLM**（small-to-big）是**必须**，不是可选优化。
- **非对称检索**：query 要加前缀 `为这个句子生成表示以用于检索相关文章：`，passage 不加。
  → 必须封成 `embed_query()` / `embed_passage()` 两个函数，**不许合成一个**。
- 模型自带 `Normalize` 模块，输出**已 L2 归一化** → 直接用内积，别再归一化一遍。

---

## 6. 环境事实（实测，别猜）

| 工具 | 版本 / 状态 |
|---|---|
| git | 2.56.0.windows.2 |
| Python | 3.13.13（`python` 与 `py` 均可用） |
| uv | 0.12.2 |
| Node | v24.15.0 |
| pnpm | **11.22.0**（用这个，不用 npm） |
| npm | 12.0.1 |
| Docker | 29.8.0 + Compose v5.5.1 ✅ |
| psql | ❌ 未安装 → 用 `docker compose exec db psql` |
| 系统 | Windows；shell 以 PowerShell 为主，路径用反斜杠 |

**bge 模型本地路径**（标准 HF cache，符号链接完好，可直接加载）：

```
C:\Users\MOM\Desktop\asklaw\models\hf\hub\models--BAAI--bge-large-zh-v1.5\snapshots\79e7739b6ab944e86d6171e44d24c997fc1e0116
```

> 该目录共 **2.43 GB**，因为同一套权重存了两种格式、分在两个快照里：
> - `snapshots\79e7739b…`（`refs/main` 指向它）→ 用 `pytorch_model.bin`（1241.9 MB）
> - `snapshots\0cc67d9f…` → 用 `model.safetensors`（1241.8 MB）
>
> ⚠️ **不要删 79e7739b 里的 `pytorch_model.bin`** —— 那个快照里没有 safetensors，删了模型就加载不了。
> 要省空间应删另一个快照 `0cc67d9f…` 及其 blob（省 1.24 GB），并确认 `refs/main` 仍指向 79e7739b。
> **模型路径写进 `.env.example` 的 `EMBEDDING_MODEL_PATH`，代码里不许硬编码这个绝对路径。**

### 网络（很重要）

- **`github.com` 直连不通**（`schannel` 报错或超时）。
- 系统代理 `http://127.0.0.1:21882` 是**主人自己的代理工具，默认关闭**。
  要外网时先问主人是否已开启，**不要假设它可用**（2026-10-28 实测它处于关闭状态，设了反而全挂，见 `docs/adr/0002-parsing-stack.md` §3.4）。
- 让命令走通代理：

```powershell
$env:HTTP_PROXY='http://127.0.0.1:21882'
$env:HTTPS_PROXY='http://127.0.0.1:21882'
$env:NODE_USE_ENV_PROXY='1'   # Node 24 的 fetch 才认环境代理
```

- 因此：**依赖尽量走国内镜像**（pip 用清华源、npm 用 npmmirror、pnpm 配 registry）。

---

## 7. 常用命令

```powershell
# ---------- 依赖服务 ----------
docker compose up -d                 # postgres + redis
docker compose logs -f backend       # 看后端日志
docker compose exec db psql -U baleen -d baleen   # 进数据库

# ---------- 后端 ----------
cd backend
uv sync                              # 装依赖
uv add <pkg>                         # 加依赖（先说明理由！）
uv run alembic upgrade head          # 迁移
uv run alembic revision --autogenerate -m "xxx"
uv run fastapi dev                   # 起开发服务

# ---------- 前端 ----------
cd frontend
pnpm install
pnpm dev
pnpm build

# ---------- 评测 ----------
python eval/scripts/candidates.py --samples eval/samples --out eval/candidates.csv
python eval/scripts/runner.py --samples eval/samples --out eval/preds.csv --predictor fake
python eval/scripts/metrics.py --labels eval/labels.csv --preds eval/preds.csv --split test
```

---

## 8. 明确不做（别自己加戏）

| 不做 | 原因 |
|---|---|
| 注册 / 登录 / 邮箱验证 / 找回密码 | 单用户；毕设评分不看这个 |
| 多租户 / 计费 / 配额 / 管理后台 | 同上 |
| 自建向量库、自研 agent 框架 | 用现成的，省时间 |
| 实时协作、移动端、浏览器插件 | 超出范围 |
| 站内 WYSIWYG 编辑 | v2 再说；真源是 block JSON，随时能加 |
| Elasticsearch / Meilisearch | 中文召回不满意了再说 |
| 让 agent 自主决定导入流程的下一步 | 必须是确定性状态机 |

---

## 9. 代码规范

**Python**
- `ruff` 格式化 + lint，行宽 100
- 类型标注必须写（`mypy` 宽松模式）
- 分层：`api/`（路由）→ `service/`（业务）→ `repo/`（数据访问，**唯一允许发查询的地方**）
- 所有查询走 `Repo(owner_id)`，**不许裸用 session 拼 SQL**
- 禁止模块级可变全局状态（`current_user` 一律靠参数传）

**TypeScript / Vue**
- Composition API + `<script setup lang="ts">`，禁止 Options API
- `vue-tsc` 类型检查必须过
- API 调用统一走 `src/api/` 封装，组件里不许直接 `fetch`

**提交信息**（Conventional Commits）

```
feat(backend): 加 G1 价值闸的启发式打分
fix(frontend): 修上传进度条在失败时不回零
docs(adr): 记录为什么不用 MongoDB
eval: 补 10 条测试文档类样本
chore: 升级 uv 到 0.13
```

---

## 10. 给 AI agent 的开工流程

**每次新会话，照这个顺序：**

1. 读 `AGENTS.md`（本文件）→ 读 `docs/architecture.md`（若已存在）
2. 明确你要动的是哪个顶层目录，说出来
3. 列出你打算创建/修改的文件清单 → **等人确认**
4. 动手，只碰清单里的文件
5. 自测：命令能跑通、类型检查过、没碰 `eval/samples/`
6. 交付时说明：改了什么、为什么、怎么验证的

**评测任务**另有专门的任务书：`eval/TASK.md`（含分阶段要求，**不要一次做到尾**）。

---

## 11. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08 | 初版：技术栈与架构决策落定，评测脚手架启动 |
| 2026-08 | 加硬约束 11：禁止 AI 修改本文件（防止 agent 自我扩权） |
| 2026-08 | 修正 §6 模型目录说明：原「删 pytorch_model.bin 可省 1.2G」是错的，会毁掉 79e7739b 快照；并补充"权重永不进 git" |
| 2026-10-28 | 解析栈定稿（ADR 0002）：pymupdf / python-docx / markdown-it-py / python-pptx；`.doc` 不支持；§6 代理条目改为"主人自己的工具、默认关闭" |
| 2026-10-28 | 解析栈补充：`.txt` 支持（编码探测 UTF-8→GB18030）；纯图片归 `no_text_expected` 而非 unsupported；扫描件判定改用最大页字符数 |
