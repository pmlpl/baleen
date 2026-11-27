# AGENTS.md — baleen 项目约定

> **任何 AI 编码代理（Trae / WorkBuddy / Claude Code / DSH / Cursor…）在动手前必须读完这份文件。**
> 这份文件是唯一的约定来源。里面写"已锁定"的东西，不要重新讨论、不要擅自替换。
>
> **本文件可被任何 agent 修改**（2026-11-07 主人授权，解除原「⛔ 只能由人类修改」限制）。
> 但每次改动必须：① 写进 §11 变更记录（日期 + 改了什么 + 为什么）；
> ② 在当次回复里说清改了哪条、为什么。**无痕改动视为破坏约定。**

---

## 1. 这是什么

**baleen** —— **个人**智能笔记库。

一句话定位：**一个能自己判断该不该收、收了之后还能被追问出处的本地知识库。**

- 交付背景：本科毕业设计（软件过程技术方向）
- 场景：**多用户（本地）**。用户名+密码+JWT，数据按 `owner_id` 隔离；不做计费/第三方登录
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
| `eval/inputs/samples/` | **真实样本文件** | ❌ **任何人都不许动** |

**一句话边界**：一次任务只碰一个顶层目录；要跨目录，先说明为什么。

---

## 3. 硬约束（违反即返工）

1. **一次任务只碰一个顶层目录**（`backend/` / `frontend/` / `docs/` / `eval/` 之一）。
2. **不许擅自新增第三方依赖**。需要时先给出：用途、体积、维护状态、替代方案。等确认。
3. **不许创建或修改 `.env`**。只能改 `.env.example`。
4. **不许改 `eval/inputs/samples/` 下任何文件** —— 包括重命名、移动、修改内容。**文件名本身就是评测特征**，改了就等于毁掉样本。
5. **不许把任何密钥、token、密码写进代码或提交历史。**
6. 数据表结构变更**必须**带 Alembic migration，不许手改数据库。
7. 改完必须能跑通：`docker compose up -d` → 后端 `GET /healthz` 返回 200。
8. **不许推翻第 4、5 节已锁定的技术栈与架构**。要换，先写 `docs/adr/` 提案，等人确认。
9. **不许提前实现未列入范围的功能**（见第 8 节"明确不做"）。
10. Python 侧统一用 `uv` 管理依赖，不许混用 `pip install` / `requirements.txt`。
11. **改 `AGENTS.md` 必须留痕。** 允许 agent 修改本文件（主人 2026-11-07 授权），但每次改动都要：
    ① 在 §11 变更记录加一行（日期 + 改了什么 + 为什么）；
    ② 在当次回复里明示「改了哪条、为什么」。
    **借改本文件放宽自己的约束**（删硬约束、放开权限、跳过流程）等于破坏约定 —— 必须由主人在当次对话中明确授权才能做，且同样留痕。

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

> **完整论证见 `docs/adr/0001-architecture-baseline.md`**
> 那里有每一条的「代价」和「何时该推翻」，还有 **10 个被明确否决的备选方案**（答辩素材）。
> 下面只列结论，方便速查。

1. **PostgreSQL 单库**承载业务 + 向量 + 全文；**不引入独立向量库**
2. **笔记真源 = block JSON**（TipTap 兼容），markdown 只是渲染
3. **Embedding 用本地 `bge-large-zh-v1.5`** —— 硬后果见下方警告
4. **导入是确定性状态机**，不是 agent loop（必须可重放、可断点续跑）
5. **审核拆三道闸**：G0 解析 / G1 价值 / G2 归属 —— 便宜的先跑、早退
6. **G2 判「有没有可复用知识」**，不判「属于哪个学科」
7. **charter 是自然语言**；代码里**永远不许出现**领域枚举字段
8. **单用户 → 默认收 + 标质量**，不是默认拒（误拒代价 ≫ 误收代价）
9. **LLM 全走 OpenAI 兼容协议** + adapter + 能力标记
10. **embedding 记录 `model + dim`**，换模型 = 新建表重建，绝不原地覆盖
11. **业务表按 `owner_id` 隔离**（由 token 解出，曾写死 1）；查询只走 `Repo(owner_id)`
12. **数据模型的 6 张表**见 `docs/adr/0003-data-model.md`

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

**bge 模型本地路径**（已从 `asklaw` 搬到本项目工作区，**自包含**）

```
<本地路径，由 .env 的 EMBEDDING_MODEL_PATH 指定>
```

> 1.21 GB / 11 个文件，由 `asklaw` 的 HF 缓存快照**展开符号链接**后复制而来。
> 之所以搬：`asklaw` 项目随时可能被删或移动，项目不该依赖它。
>
> ⚠️ **`models/` 在工作区根，不在 `baleen/` 仓库内** —— 1.2 GB 权重永不进 git。
> 实测：`SentenceTransformer(该路径, device="cuda")` **1.5s 加载**，输出 `(N, 1024)`、L2 模长 `1.0000`、
> `max_seq_length=512`（确认了 512 token 上限，切块必须 ≤400 字）。
>
> **路径写进 `.env` 的 `EMBEDDING_MODEL_PATH`，代码里不许硬编码。**

---

### 网络（很重要）

- **`github.com` 直连不通**（`schannel` 报错或超时）。
- 系统代理 `http://127.0.0.1:<代理端口>` 是**主人自己的代理工具，默认关闭**。
  要外网时先问主人是否已开启，**不要假设它可用**（2026-10-28 实测它处于关闭状态，设了反而全挂，见 `docs/adr/0002-parsing-stack.md` §3.4）。
- 让命令走通代理：

```powershell
$env:HTTP_PROXY='http://127.0.0.1:<代理端口>'
$env:HTTPS_PROXY='http://127.0.0.1:<代理端口>'
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
uv run alembic check                 # 模型↔迁移一致性(有漂移即非零退出);改了模型就必须跑
uv run alembic revision --autogenerate -m "xxx"   # ⚠️ 生成后必须人工 review:迁移里用 op.execute 裸 SQL 建的索引,模型里没声明就看不见,会被 autogenerate 顺手 drop
uv run fastapi dev                   # 起开发服务

# ---------- 前端 ----------
cd frontend
pnpm install
pnpm dev
pnpm build

# ---------- 测试 ----------
cd backend                           # ⚠️ 必须:pyproject 是 pythonpath=["."],从仓库根跑 = No module named 'app'
uv run pytest -q                     # 全量(需 docker compose up -d)
docker compose exec db psql -U baleen -d postgres -c "CREATE DATABASE baleen_ci"   # 空库
DATABASE_URL="postgresql+asyncpg://baleen:baleen@localhost:5432/baleen_ci" \
  uv run alembic upgrade head        # 空库迁移到 head
DATABASE_URL="postgresql+asyncpg://baleen:baleen@localhost:5432/baleen_ci" \
  uv run pytest -q                   # ⚠️ 空库全量 = CI 同口径,见下

# ---------- 评测 ----------
python eval/scripts/candidates.py --samples eval/inputs/samples --out eval/inputs/candidates.csv
python eval/scripts/runner.py --samples eval/samples --out eval/preds.csv --predictor fake
python eval/scripts/metrics.py --labels eval/inputs/labels.csv --preds eval/runs/preds.csv --split test
```

### 7.1 「空库同口径」必须跑（本地全绿 ≠ CI 绿）

开发库里有真实数据（owner 1 的 api_key、86 条自答记录、已推进的主键序列……），
测试跑在它上面会**偷偷吃到这些残留**——本地绿、CI 空库红，2026-11-27 首跑实锤：
36 个失败全在这类依赖上（35 个 api_key 前置闸 + 1 个 identity 序列 + 1 个自答总数）。

**规矩：动了 API/夹具/DB 相关测试的批次，交付前必须跑一次上面的「空库全量」，摘要行与本地全量一致才算过。**
夹具不许依赖开发库已有数据：需要什么自己造（假 key、造消息），造在事务内随夹具回滚。

---

## 8. 明确不做（别自己加戏）

| 不做 | 原因 |
|---|---|
| 邮箱验证 / 找回密码 / 第三方登录 | 只做用户名+密码；毕设评分不看更复杂的流程 |
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

**视图 script 行数闸（2026-11-26 主人拍板：上限 1000 行，语义 =「必须做决定」）**

- 每个 `frontend/src/views/*.vue` 的 `<script>` 段（含 `<script setup>`，多段累加）**不超过 1000 行**；
  `scripts/doc_lint.py` 机械检查（pre-commit 拦），超限即 ERROR。
- 超限时的处理**不是「必须拆」**，而是必须从下面选一条并留痕：
  1. **沿真实边界拆，且先测后拆**——拆前先给现有行为补 Playwright 探针当等价网
     （C1 / C2 / QaView 三次拆分都是这个模式），拆后探针跑绿再提交；
  2. **确认无自然边界 → 豁免登记**（下表），写清为什么拆不动。
     豁免 =「这个决定做过了」，不是「没人管」；
  3. **禁止为凑行数硬拆。**
- **反例（真实教训，别重复）**：QaView 的 SSE 发送循环只有 154 行，却读写父组件 **16 个状态**——
  照 props/emit 硬拆要 16 个 prop + 十几个 emit，比大文件更难读；它最后用 composable
  （共享 ref、不做管道）才拆成。**行数是信号，不是目标。**
- 豁免表（`doc_lint` 从本表读；行数已回落到阈值内必须删除该行，否则 WARN）：

| 豁免视图 | 登记时行数 | 登记日期 | 为什么拆不动 |
|---|---|---|---|
| （空） | — | — | — |

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
5. 自测：命令能跑通、类型检查过、没碰 `eval/inputs/samples/`
6. **文档同步（交付的一部分，不是可选项）**
   - 写交付块：`backend/delivery/<阶段>.md` · `frontend/DELIVERY.md` · `eval/DELIVERY.md`（格式见 `HANDOFF.md` §4）
   - 有实质工作就写当天日志：`../日志/YYYY-MM-DD.md`（规范见其 `README.md`）
   - 改了接口 → `docs/api.md` 升版本号 + 写变更记录
   - 跑 `python scripts/doc_lint.py`，**ERROR 必须为 0**（pre-commit 已挂这个闸）
7. 交付时说明：改了什么、为什么、怎么验证的、还有哪些已知限制

> **没写文档的交付不算交付。** 代码进仓库但交付块/日志缺一条，
> 下一个人就得从 `git log` 里考古——这正是 2026-11 上旬发生过的事。

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
| 2026-10-28 | 补 ADR 0001（架构基线）+ ADR 0003（数据模型）；§5 改为速查结论，论证移入 ADR 0001 避免重复 |
| 2026-10-28 | 模型自包含：bge 从 `asklaw` 搬到 `智能笔记库\models\bge-large-zh-v1.5`（展开符号链接，1.21 GB）；§6 路径与说明同步更新 |
| 2026-11-07 | **解除「禁止 AI 修改本文件」**（主人明确授权）：§3.11 改为「改必须留痕」（§11 记录 + 回复明示），头部声明同步；理由——原限制挡住的是「有痕改动」，挡不住真实漂移，而漂移只能靠改约定来治 |
| 2026-11-07 | §10 开工流程把「文档同步」列为第 6 步（原来只在第 6 步「交付说明」里含糊带过）：交付块 / 当天日志 / api.md 版本 / `doc_lint` 0 ERROR；并明确「没写文档的交付不算交付」 |
| 2026-11-26 | §9 新增「视图 script 行数闸」（**主人拍板**，WorkBuddy 复评报告 §3 P0.2 落地）：视图 `<script>` 上限 **1000 行**，`doc_lint` 机械拦；语义是「**必须做决定**」而非「必须拆」——超限时二选一：①沿真实边界**先测后拆**（补 Playwright 等价网再动），②确认无自然边界则**豁免登记**（表在 §9，须写清理由）；**禁止为凑行数硬拆**。理由：QaView 两周内「拆到 1890 → 又长回 2482 → 再拆到 832」的循环证明没有闸的约定拦不住增长；send() 读写 16 个父状态的教训证明硬拆会造出更脆的架构。阈值取 1000（非提案的 800）是主人裁决——豁免机制存在后数字只是提前量，不必让闸上线第一天就逼拆 |
| 2026-11-09 | charter.md 退役 + 笔记契约内容层：① 删 `charter.md`（charter 已问卷化存 DB，不再手写文件）；② `config.py` `DEFAULT_CHARTER` 改通用兜底（去掉"软件本科生"身份预设）；③ `docs/note-format.md` 加 §5 内容层（5 条好笔记骨架：用自己的话/原子化/定义+例子/三段可复现/出处，融合 charter 判据 + Zettelkasten）；④ `generator.py` prompt「判据」「笔记长什么样」对齐内容层；⑤ `g2.py` warning 文案 `charter.md`→章程；⑥ `api.md` v18→v19 + 优先级描述更新；⑦ NAVIGATION/HANDOFF/各 docstring 同步清理 `charter.md` 引用。跨 docs/backend/frontend/根目录，理由：charter.md 退役的引用散落全项目 |
| 2026-11-12 | §1 定位去掉「中文优先」（主人拍板：定位就是「智能笔记库」）：同步 `docs/adr/0001` §1、`backend/pyproject.toml` description、`eval/TASK.md` §一。跨根目录/docs/backend/eval，理由：同一句定位散落 4 处；测试 fixture `utf8.txt` 中的「中文优先」是 UTF-8 编码测试文本，与定位无关，不动 |
| 2026-11-18 | eval/ 目录按职责重组（inputs 标注语料 / queries 题集 / runs 跑批产物 / analysis 分析 / scripts 脚本，顶层 27→9 条目）：samples/candidates/labels/split_meta 迁入 inputs/，report/ci/preds 迁入 runs/，消融与缺陷台账迁入 analysis/；§3/§6/§10 的 eval 路径同步；eval_capture.py 归档路径随迁（532 passed，doc_lint 0/0） |

| 2026-11-27 | §7 新增「测试」命令块 + §7.1「空库同口径必须跑」：CI 首次实跑红了 36 个测试，根因**全是对开发库残留数据的隐式依赖**（35 个靠 owner 1 已有的真 api_key 过上传/视频导入的 key 前置闸、1 个靠 identity 序列已被真实数据推进过、1 个靠开发库那 86 条自答记录），空库上全暴露；夹具改为自带数据（假 key + 自造消息，事务内回滚），并补 `setval` 修正手插 id=1 的序列。理由：本地开发库有真数据 → 本地永远绿，只有空库能复现 CI；这类缺陷不会自己消失。同时 §7 原本**没有** pytest 命令（ci.yml 注释声称「与 AGENTS §7 同口径」是假的），一并补齐 |
| 2026-11-27 | §7 补 `uv run alembic check` 命令 + `alembic revision --autogenerate` 的裸 SQL 索引警告：审计发现两处**模型↔迁移漂移**（`jobs_dedupe_uq` 模型停在 0008 之前的老定义、迁移 0019 裸 SQL 建的 `notes_archived_at_idx` 没在模型里声明），autogenerate 下一条迁移会把它们**静默改回去**——前者会重新引入 0008 修掉的多用户互相去重。修完把「模型改了必须跑 alembic check」写进常用命令，并让 CI 装上同一道闸（本地与 CI 同口径）。理由：`upgrade head` 只证明迁移能执行，证明不了 schema 与模型一致；这道闸是该类缺陷唯一的机械守卫 |
