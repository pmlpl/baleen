# HANDOFF — 线间交接台

> **单入口，各自出口。** 所有 agent **只读**本文件；交付写在你自己目录下的 `DELIVERY.md`。
> 这样不会出现两个 agent 同时写一个文件、互相覆盖的问题。

---

## 0. 谁读、谁写（先看这张表）

| 文件 | 谁写 | 谁读 |
|---|---|---|
| `HANDOFF.md`（本文件） | **只有人类 / 协调方** | **所有 agent 都读** |
| `backend/DELIVERY.md` | 只由**后端线**（ZCode）写 | 人类读 |
| `eval/DELIVERY.md` | 只由**评测线**（Trae）写 | 人类读 |
| `docs/adr/*.md` | 对应任务的 agent | 所有人 |
| `AGENTS.md` | **只有人类** | 所有 agent 读 |

**铁律**：agent **不许编辑 `HANDOFF.md`**。要回报就把内容追加到 `backend/DELIVERY.md` 或 `eval/DELIVERY.md`。

---

## 1. 当前状态

| 线 | 执行者 | 状态 | 最近交付 |
|---|---|---|---|
| **A 环境骨架** | ZCode | ✅ 已验收 | `docker-compose.yml`、`backend/` 骨架、`frontend/` 骨架 |
| **B 解析 ADR + G0 模块** | ZCode | 🟡 结构验收通过（未跑其测试、未读其逻辑） | `docs/adr/0002`、`backend/app/service/parse/` 8 文件、`tests/` + fixtures、`scripts/parse_smoke.py` |
| **C 评测脚手架** | Trae | ✅ 已验收（`stratified-v2`） | `eval/DELIVERY.md`、`make_split.py`、`_common.py`、`report.md` |
| **D 数据模型** | ZCode | ⬜ 待开工 | 本期任务，见 §2 |

**文档现状**：`docs/adr/` 有 `0001-architecture-baseline` / `0002-parsing-stack` / `0003-data-model`，编号连续。

### 遗留待处理（不阻塞主干，但要记着）

1. **槽 7 分层要细到 (槽位, 判定) 粒度** —— 现在 test 里槽 7 只有发票，2 张手写笔记全在 dev，误拒率 0% 是假象
2. **切块参数要实验校准** —— 250–400 字是从 512 token 上限推导的，没做过实验；前置依赖是 20 个 query 集
3. **线 B 的 pytest 与 smoke 脚本没人跑过** —— 结构验收≠功能验收
4. **前端只有空白首页** —— 主干通了才动

---

## 2. 待处理

### 给 ZCode（线 B：数据模型落地）

```
开工先按顺序读三份文档：
1. baleen/AGENTS.md                              → §3 硬约束、§5 决策、§6 环境事实
2. baleen/docs/adr/0003-data-model.md            → 本任务的完整规格（6 张表 DDL + 索引）
3. baleen/docs/adr/0001-architecture-baseline.md → 决策 1/11/12 的背景，可略读

任务：落地数据模型。**只动 baleen/backend/**。

交付 4 样：

1. SQLAlchemy 2.x 模型（app/models/）：sources / review_runs / notes / chunks / embeddings / jobs
   字段与类型严格照 ADR 0003 §2，不许自行增删列。

2. Alembic 迁移（alembic/versions/0002_*.py，挂在 0001_baseline 之后）：
   - op.execute("CREATE EXTENSION IF NOT EXISTS vector")
   - op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
   - 6 张表
   - ADR 0003 §5 的全部索引（含 chunks 的 trgm GIN、embeddings 的 HNSW halfvec_cosine_ops）
   - CHECK 约束：枚举列一律用 TEXT + CHECK（不用 PG 原生 ENUM）；
     chunks 要保证 (parent_id IS NULL) = is_parent

3. app/repo/base.py：Repo(owner_id) 薄封装骨架 —— 以后所有查询的唯一入口

4. app/core/config.py 补 embedding_model_path 字段
   （.env.example 里已经有这个变量，代码一直没读它，这次一起补上）

不要做：不加 API 端点、不写业务逻辑、不做切块、不动 eval/、不动 eval/samples。

自测（三样都要贴原始输出到 DELIVERY.md）：
- uv run alembic upgrade head
- docker compose exec db psql -U baleen -d baleen -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
- docker compose exec db psql -U baleen -d baleen -c "\d+ chunks"

交付写到 baleen/backend/DELIVERY.md（上次漏了这个文件，这次必须写）。
```

---

## 3. 卡住时找谁

**遇到下面「必须停下问」的，停下来把问题写进你目录下的 `DELIVERY.md`，等人回答。不要自己试一堆方案。**

| 🔴 必须停下问主人 | 🟢 自己决定，做完记一句 |
|---|---|
| 要新增任何第三方依赖（哪怕只有 1MB） | 文件/函数的命名、目录细分 |
| 想换技术方案（会偏离 `AGENTS.md` §4/§5 或 ADR） | 测试用例怎么写、断言什么 |
| 想动 `eval/samples/` 里任何文件 | 代码风格、注释多寡 |
| 想改 `AGENTS.md`、`TASK.md`、`labels.csv` 的人工标注列 | 报错文案的中文措辞 |
| 需要开代理 / 需要外网下载（主人得手动开） | 日志级别、临时脚本放哪 |
| 评测口径的理解有分歧 | 重构内部实现但不改对外接口 |
| 发现评测集本身有问题（样本坏了、标注矛盾） | 依赖版本的小幅升级（同 major） |
| 想到"我顺手也把 X 做了" 中的 X 不在任务书里 | 性能优化（不影响正确性时） |

**几个已经拍过板、不要再问的**：

- `.doc` 不支持 → G0 报「请转成 .docx」（已定，别再讨论 LibreOffice）
- `.xlsx` 暂不支持（已定）
- 纯图片 / 扫描件 → `no_text_expected`，不做 OCR（已定）
- 模型权重不进 git，路径走 `EMBEDDING_MODEL_PATH`（已定）
- 不做登录 / 多用户 / 计费（已定，见 `AGENTS.md` §8）

---

## 4. 交付写在哪

做完后，**追加**到你目录下的 `DELIVERY.md`（没有就建），格式：

~~~markdown
## 2026-10-28 14:30 · 线 B · G0 解析模块

### 产物
- backend/app/service/parse/{__init__,types,blocks,pdf_parser,docx_parser,md_parser,pptx_parser,router}.py
- backend/tests/service/parse/test_parse.py + fixtures/
- backend/pyproject.toml（uv add pymupdf python-docx markdown-it-py python-pptx）

### 自测
- `uv run pytest tests/service/parse -q` → 12 passed
- `uv run python scripts/parse_smoke.py --samples ../eval/samples` → 35 个文件，状态分布：ok=24 no_text_expected=4 unsupported_format=7 error=0

### 已知限制
- 加密 PDF 返回 error（message="PDF 已加密，无法解析"）
- pptx 演讲者备注未抽取

### 需要确认的问题
- （无）
~~~

**低于这个信息量的交付，人家会打回。**

---

## 5. 已验收的反馈（历史）

| 日期 | 对象 | 反馈 |
|---|---|---|
| 2026-10-28 | ZCode 线 A | 骨架合格：`pgvector/pgvector:pg17` 选对、`/healthz` 真探活、repo 层隔离、无擅自加依赖。**唯一缺口**：`config.py` 缺 `embedding_model_path` 字段（`.env.example` 有，代码没读） |
| 2026-10-28 | ZCode 线 B | ADR 0002 质量很高：有实测数字（速度差 20–40 倍、表格 22 vs 26）、挖出真坑（pdfplumber 裸文本流交错、`upright` 对斜向水印失效、PaddleOCR 的 mkldnn 崩溃）。决策与评测集冲突已处理：补 `python-pptx`、`.txt` 支持 |
| 2026-10-28 | Trae 线 C（阶段 1-2） | 纪律好：做到阶段 1 就停；发现数据不一致**没有擅自改**，主动上报 |
| 2026-10-28 | Trae 线 C（阶段 3） | `report.md` 合格：10 节结构、误拒率/误丢率置顶、非法组合单列。**但 test 集 split 未分层**（槽 6/9/11/12/13/14 全在 dev，partial 0 条）→ 已要求重做，`stratified-v2` 已修复 |
| 2026-10-28 | Trae 线 C（`stratified-v2`） | 14 个槽位在 test 各 ≥1、配对样本同 split、代码去重（1197 行）、主动列 3 条已知限制。**遗留**：槽 7 分层仍未细到 (槽位, 判定) 粒度，误拒率 0% 是假象 |

---

## 6. 人类怎么用这份文件

对任何 agent 只需要说一句：

```
读 baleen/HANDOFF.md 的 §0 和 §2「给 XXX」那一节，照做。
交付按 §4 格式写到 DELIVERY.md。
```

反馈方向：

```
人家（协调方） → 写 §2 待处理
agent         → 读 §2，做完写自己目录的 DELIVERY.md
人类          → 把 DELIVERY.md 甩给人家验收，结论进 §5
```

**本文件是唯一入口。** 技术决策在 `docs/adr/`，评测口径在 `eval/TASK.md`，项目约定在 `AGENTS.md`。
