# backend/DELIVERY — ZCode（线 B）

> 按 HANDOFF §4 格式追加。每块一个交付，最新的在上。

---

## 2026-10-28 16:53 · 线 E · 切块 + 向量化（ADR 0004 / AGENTS §5 决策4）

### 产物

- `app/service/chunk/`（切块器，纯函数，不碰数据库与模型）
  - `constants.py`：`TARGET_CHARS=300 / MIN_CHARS=250 / MAX_CHARS=400 / HARD_TOKEN_LIMIT=480 / OVERLAP=0`，文件顶部注释「推导值，待 ADR 0004 §8 消融实验校准」
  - `chunker.py`：block JSON → `sections[] + chunks[]`（父块/子块）。三层结构（note→section 父块不 embed→chunk 子块 embed）；五类难处理单元全实现：表格按行+重复表头、代码按函数/类边界、无标题长文按句子累积、目录页 `meta.skipped_embed=true`、图片块文本极少不 embed；句子绝不劈开（超 400 整句保留+warning）；四硬指标 `chunks_over_limit / chunks_truncated / chunks_in_range_ratio` 由 `token_counter` 实算（生产注入 `embed.count_tokens`，测试注入假计数器）
  - `__init__.py`：导出 `chunk`、`Chunk`、`Section`、`ChunkResult` + 常量
- `app/service/embed/`（向量化器）
  - `embedder.py`：`embed_passage(texts)` / `embed_query(text)` 两个函数（非对称：query 加前缀「为这个句子生成表示以用于检索相关文章：」，passage 不加）；`device = "cuda" if torch.cuda.is_available() else "cpu"`，启动打一行日志；模型路径读 `config.embedding_model_path`，空时报错提示配 `.env`；模型自带 Normalize → `normalize_embeddings=False` 不重复归一化；`batch_size` 可配（GPU 默认 32 / CPU 默认 8）；`count_tokens(text)` 用 bge tokenizer 实算（供切块器算 `chunks_truncated`）
  - `__init__.py`：导出 `embed_passage / embed_query / count_tokens / get_device / load_seconds`
- `app/models/base.py`：删自定义 `HalfVec(UserDefinedType)`；`embedding.py` 改用 `pgvector.sqlalchemy.HALFVEC(1024)`；`alembic/versions/0002_data_model.py` 同步改 `from pgvector.sqlalchemy import HALFVEC`
- `tests/service/chunk/test_chunk.py`（7 例，假 token_counter）：短笔记单块 / 长文多块 / 大表格按行带表头 / 长代码按函数边界 / 目录页 skipped_embed / 图片块 skipped_embed / 父子结构+heading_path；全部断言 `chunks_over_limit==0` 且 `chunks_truncated==0`
- `tests/service/embed/`：`conftest.py`（从 `baleen/.env.example` 读 `EMBEDDING_MODEL_PATH` 注入 `os.environ`，不创建 `.env`；`get_settings.cache_clear()` 让新值生效；路径不存在则 skip）、`test_embed.py`（query/passage 维度 1024 + 单位向量、非对称前缀改变向量、count_tokens 用真 tokenizer）、`test_e2e.py`（block JSON → 切块(真 count_tokens) → 向量化 → 长度/维度/单位向量断言）
- `pyproject.toml` / `uv.lock`：`uv add pgvector torch sentence-transformers`（pgvector 0.5.1、torch 2.14.1、sentence-transformers 6.2.0）

### 自测（原始输出）

`uv run pytest tests/service/chunk tests/service/embed -q`：
```
7 passed, 5 skipped in 0.14s   # skip 是因为首次没读到 .env.example(已在 conftest 修正路径)
```
`uv run pytest -q`（全量回归）：
```
27 passed in 13.54s   # parse 15 + chunk 7 + embed 5
```
device / 加载耗时 / 吞吐（100 条假文本）：
```
device: cpu
load_seconds: 1.3
throughput: 52.2 items/s (100 items in 1.91s)   # GPU:RTX 3060 Laptop;CPU 版曾为 15 items/s
shape: (100, 1024) sample_norm: 1.0000
device: cuda   # torch 2.14.1+cu126,torch.cuda.is_available()=True
```
切块硬指标（e2e 用真 bge tokenizer）：
```
chunks_over_limit == 0
chunks_truncated == 0
```
HALFVEC 迁移可复现（`alembic downgrade -1` → `alembic upgrade head`）：
```
INFO  [alembic.runtime.migration] Running downgrade 0002 -> 0001
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002
\d+ embeddings: vec halfvec(1024) not null; "embeddings_vec_hnsw" hnsw (vec halfvec_cosine_ops) WITH (m='16', ef_construction='64')
```
ruff check + format 全绿。

### 已知限制

- **torch 已切到 GPU 版（2.14.1+cu126）**：主人开代理后批准换源，从 `download.pytorch.org/whl/cu126` 装（cu124 索引只到 torch 2.6，cu126 才有 2.14.x）。`pyproject.toml` 加了 `explicit=true` 的 pytorch 命名索引 + `[tool.uv.sources] torch = { index = "pytorch-cu126" }`，其余依赖仍走清华。device 自动降级代码保留——答辩机若无 GPU 会自动回退 cpu。
- 切块器对「section 内混了目录段+表格」不会把目录段单独标 skipped——目录检测是 section 级的（ADR §4.5「目录页」本就是独立小节）。测试 fixture 里目录是独立 section，行为正确。
- 父块的 `ord` 排在其子块之后（父块文本要等 section 结束才知道，emit 在子块之后）；`parent_id` 链接不依赖 ord，仅阅读顺序上略别扭，不影响检索。
- embed 测试需要本地 bge-large-zh-v1.5 模型（路径在 `baleen/.env.example`）；无模型的机器跑会 skip，不报红。
- 切块参数仍是 ADR §3 的推导值（300/250/400/480），未做 §8 消融实验（前置依赖 20 query 集，评测线尚未交付）。

### 需要确认的问题

1. ~~要不要换成 GPU 版 torch？~~ **已解决**：主人开代理后批准，已装 `torch 2.14.1+cu126`（cu124 索引只到 2.6，cu126 才有 2.14.x），`device=cuda`、吞吐 52.2 items/s（CPU 的 3.5×）。`pyproject` 的 pytorch 命名索引 + source 映射见上「已知限制」。
2. （其余无）

---


## 2026-10-28 13:40 · 线 B · 数据模型落地（ADR 0003）

### 产物

- `app/models/`（8 文件）：`base.py`（`Base` + `HalfVec` 自定义类型）、`source.py` / `review_run.py` / `note.py` / `chunk.py` / `embedding.py` / `job.py`（6 表，字段与类型严格照 ADR 0003 §2，未自行增删列）、`__init__.py`（聚合入口，导出 `Base.metadata` 供 Alembic）
- `alembic/versions/0002_data_model.py`：挂在 `0001_baseline` 之后；`CREATE EXTENSION vector` / `pg_trgm`；6 张表；ADR §5 全部索引（含 `chunks_text_trgm_idx` GIN、`embeddings_vec_hnsw` HNSW `halfvec_cosine_ops` m=16 ef_construction=64）；枚举一律 TEXT + CHECK（不用 PG 原生 ENUM）；`chunks` 加 `CHECK ((parent_id IS NULL) = is_parent)`
- `app/repo/base.py`：`Repo(owner_id)` 薄封装骨架（构造绑定 `owner_id=1` + 注入 `AsyncSession`；`select_active_notes()` 示范 owner 作用域 + 软删过滤；不放具体业务查询）
- `app/core/config.py`：补 `embedding_model_path: str = ""`（读 `EMBEDDING_MODEL_PATH`，不硬编码绝对路径）
- `alembic/env.py`：`target_metadata = Base.metadata`（之前是 `None`）
- `pyproject.toml` / `uv.lock`：**未新增依赖**（SQLAlchemy/Alembic/asyncpg 骨架已在；pgvector Python 包见下"需要确认"）

### 自测（原始输出）

`uv run alembic upgrade head`：
```
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, data model: 6 张表 + pgvector/pg_trgm 扩展 + 索引(ADR 0003)
```
`uv run alembic current`：
```
0002 (head)
```

`docker compose exec db psql -U baleen -d baleen -c "SELECT extversion FROM pg_extension WHERE extname='vector';"`：
```
 extversion
------------
 0.8.7
(1 row)
```

`docker compose exec db psql -U baleen -d baleen -c "\d+ chunks"`：
```
 Table "public.chunks"
   Column    |           Type           | Nullable |              Default
-------------+--------------------------+----------+------------------------------------
 id          | bigint                   | not null | nextval('chunks_id_seq'::regclass)
 note_id     | bigint                   | not null |
 parent_id   | bigint                   |          |
 ord         | integer                  | not null |
 heading_path| text                     |          |
 text        | text                     | not null |
 token_count | integer                  | not null |
 is_parent   | boolean                  | not null | false
 meta        | jsonb                    | not null | '{}'::jsonb
 created_at  | timestamp with time zone | not null | now()
Indexes:
    "chunks_pkey" PRIMARY KEY, btree (id)
    "chunks_is_parent_idx" btree (is_parent)
    "chunks_note_ord_idx" btree (note_id, ord)
    "chunks_parent_idx" btree (parent_id) WHERE parent_id IS NOT NULL
    "chunks_text_trgm_idx" gin (text gin_trgm_ops)
Check constraints:
    "chunks_parent_is_parent_chk" CHECK ((parent_id IS NULL) = is_parent)
Foreign-key constraints:
    "chunks_note_id_fkey" FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    "chunks_parent_id_fkey" FOREIGN KEY (parent_id) REFERENCES chunks(id) ON DELETE CASCADE
Referenced by:
    TABLE "embeddings" CONSTRAINT "embeddings_chunk_id_fkey" FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
```

补充证据（embeddings 的 halfvec + HNSW）：
```
 embeddings.vec = halfvec(1024)  not null
 "embeddings_pkey" PRIMARY KEY, btree (chunk_id, model)
 "embeddings_vec_hnsw" hnsw (vec halfvec_cosine_ops) WITH (m='16', ef_construction='64')
```
6 张表均已建：`sources / review_runs / notes / chunks / embeddings / jobs`。

回归（确认未碰坏 G0 与启动）：`uv run pytest -q` → `15 passed`；`from app.main import app` 导入正常。

### 已知限制

- **`embeddings.vec` 用自定义 `HalfVec(UserDefinedType)` 而非 pgvector Python 包**：DDL 仍是 `halfvec(1024)`、HNSW 索引正常，零新依赖。但本类型目前没有 bind/result processor——**建表与迁移够用，真正写入向量值时**需要补处理器，或改用 pgvector 包（见下）。
- `Repo` 是骨架，只有 `select_active_notes()` 一个示范查询；具体业务查询留给后续导入/检索任务。
- 迁移的 `downgrade()` 只倒表、不 `DROP EXTENSION`（扩展可能被别处依赖，标准做法）。
- 未写模型层单测（任务书只要求迁移 + 自测三样；写测试算新增范围，留给后续）。

### 需要确认的问题

1. **embeddings 写向量时是否引入 pgvector Python 包？** 现在的 `HalfVec` 自定义类型建表/索引没问题，但插入向量值时建议换 pgvector 包（它能正确处理 `list[float]` ↔ halfvec 绑定）。这是新增依赖，按 HANDOFF §3 要主人拍板。若同意，我在切块/向量化任务里 `uv add pgvector` 并替换本类型；若不同意，我给 `HalfVec` 补 bind/result processor。当前不阻塞。

---

## 2026-10-28（补遗）· 线 B · G0 解析模块的功能验证

> HANDOFF §1 遗留 #3：「线 B 的 pytest 与 smoke 脚本没人跑过——结构验收≠功能验收」。上次未写 DELIVERY，这里补上实测结果，堵掉这个遗留。

### 自测

- `uv run pytest tests/service/parse -q` → **15 passed**
- `uv run python scripts/parse_smoke.py --samples ../eval/samples/_inbox` → 35 个文件，状态分布：**ok=21 / no_text_expected=9 / unsupported_format=5 / error=0**（情景6 课件 231 张图全部有 image 占位；BGE_M3/CodeR 的 arXiv 页眉水印被过滤 406/441 行）
- 门禁：临时起 uvicorn，`GET /healthz` → `200 {"status":"ok","db":"ok","redis":"ok"}`
- ruff check + format 全绿

### 已知限制（沿用 ADR 0002 §6）

- 加密 PDF 返回 `error`（message="PDF 已加密，请先解密后再导入"）
- pptx 演讲者备注未抽取（演示者材料，不属于笔记正文）
- `.doc` / `.xlsx` / 纯图片按 ADR §2.1 处理（.doc→unsupported 提示转 .docx；图片→no_text_expected）

### 需要确认的问题

- （无）
