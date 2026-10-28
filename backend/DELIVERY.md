# backend/DELIVERY — ZCode（线 B）

> 按 HANDOFF §4 格式追加。每块一个交付，最新的在上。

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
