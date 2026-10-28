# ADR 0003：数据模型

- **日期**：2026-10-28
- **状态**：已采纳（等人确认后据此写 Alembic 迁移）
- **关联**：ADR 0001（决策 1 / 3 / 11 / 12 在这里落地）、ADR 0004（切块规则，`chunks` 表的行为说明）

---

## 1. 背景

主干要跑通的最小闭环是：

```
上传 → 解析(G0) → 判定(G1/G2) → 入库(切块+向量化) → 检索 → 问答
```

这个闭环需要记住四类东西：**文件从哪来、判定是什么、笔记长什么样、块和向量怎么存**。
本 ADR 定这四类东西的表结构，以及一个任务队列。

**设计目标**：能支撑 1 万篇笔记 / 2.5 万块，且**换 embedding 模型时有退路**。

---

## 2. 决策：6 张表

### 2.1 `sources` — 原始文件

```sql
CREATE TABLE sources (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      BIGINT      NOT NULL DEFAULT 1,
    sha256        CHAR(64)    NOT NULL,              -- 内容寻址，用于秒级去重
    filename      TEXT        NOT NULL,              -- 原始文件名，含"新建 文本文档.txt"这种脏名
    ext           TEXT        NOT NULL,              -- 小写、不含点
    size_bytes    BIGINT      NOT NULL,
    storage_path  TEXT        NOT NULL,              -- storage/{sha256[:2]}/{sha256}
    mime          TEXT,
    parse_status  TEXT        NOT NULL DEFAULT 'ok', -- ok|no_text_expected|unsupported_format|error|pending_ocr
    parse_meta    JSONB       NOT NULL DEFAULT '{}', -- pages/chars/engine/warnings
    doc           JSONB,                             -- 解析出的原始 block JSON（未编辑）
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sources_sha_uq UNIQUE (owner_id, sha256)
);
```

**要点**：
- `sha256 UNIQUE` → 重复导入在**第一行 SQL** 就被拦下，不浪费解析和 LLM 调用
- `filename` **原样保存，不清洗**。它是判定特征（"霸道总裁爱上我.doc"实际是学习笔记），也是槽 14 的评测点
- `doc` 存**解析结果**，与 `notes.doc`（判定后决定入库的内容）分开：被拒的文件也要能查到解析出了什么

### 2.2 `review_runs` — 判定记录

```sql
CREATE TABLE review_runs (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT      NOT NULL DEFAULT 1,
    source_id    BIGINT      NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    gate         TEXT        NOT NULL,               -- G0|G1|G2
    verdict      TEXT        NOT NULL,               -- accept|partial|reject|skip
    quality      TEXT,                               -- core|normal|reference|junk
    note_type    TEXT,
    score        NUMERIC(4,3),
    action       TEXT,                               -- index|extract_spans|topic_dialog|drop
    reasons      JSONB       NOT NULL DEFAULT '[]',  -- 结构化理由，可审计
    model        TEXT,                               -- G2 用的哪个模型（G0/G1 为 NULL）
    cost_tokens  INT         NOT NULL DEFAULT 0,
    latency_ms   INT         NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**要点**：
- **一个闸门一行**（不是一次判定一行）→ 能统计"每个闸门的通过率"和"垃圾在哪一层被拦住"
- `reasons` 用 JSONB 而不是纯文本 → 以后能按理由类型做统计（"多少条因'无上下文'被拒"）
- `action` 只有 4 个值。**待 OCR 不在这里**（见决策 ③）

### 2.3 `notes` — 笔记（真源）

```sql
CREATE TABLE notes (
    id         BIGSERIAL PRIMARY KEY,
    owner_id   BIGINT      NOT NULL DEFAULT 1,
    source_id  BIGINT      REFERENCES sources(id) ON DELETE SET NULL,  -- 手写笔记为 NULL
    title      TEXT        NOT NULL,
    doc        JSONB       NOT NULL,               -- ★ 真源：block JSON
    markdown   TEXT,                               -- 渲染缓存，可随时重建
    lang       TEXT,                               -- zh|en|mixed
    tags       TEXT[]      NOT NULL DEFAULT '{}',
    quality    TEXT,                               -- 从 review_runs 继承
    status     TEXT        NOT NULL DEFAULT 'active',  -- active|archived
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**要点**：
- `doc` 是**真源**，`markdown` 只是缓存。任何时候都可以从 `doc` 重建 `markdown`
- `source_id` 可空且 `ON DELETE SET NULL` → 删原始文件不该连带删笔记
- `status` 做**软删除**（决策 ⑤）

### 2.4 `chunks` — 块（父子同表）

```sql
CREATE TABLE chunks (
    id           BIGSERIAL PRIMARY KEY,
    note_id      BIGINT   NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    parent_id    BIGINT   REFERENCES chunks(id) ON DELETE CASCADE,  -- NULL = 父块
    ord          INT      NOT NULL,                 -- 在本篇笔记里的顺序
    heading_path TEXT,                              -- "三、闭包的坑 > 3.1 内存泄漏"
    text         TEXT     NOT NULL,
    token_count  INT      NOT NULL,
    is_parent    BOOLEAN  NOT NULL DEFAULT false,   -- 冗余列，方便查询
    meta         JSONB    NOT NULL DEFAULT '{}',    -- block 类型、表格行列、来源页号
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**要点**：
- **父子同表**：两者的字段完全一样，唯一区别是"父块不 embed"。合表省一次 join、省一处冗余定义
- `parent_id IS NULL` ⇔ `is_parent = true`（后者是冗余列，为查询方便；用 CHECK 约束保证一致）
- `heading_path` 是**引用溯源**的关键：回答里能说"出自《闭包的坑》第 3.1 节"

### 2.5 `embeddings` — 向量（独立表）

```sql
CREATE TABLE embeddings (
    chunk_id   BIGINT      NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model      TEXT        NOT NULL,                -- 'bge-large-zh-v1.5'
    dim        SMALLINT    NOT NULL,                -- 1024
    vec        halfvec(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model)                   -- 同一块、同一模型只有一条
);
```

**要点**：
- **只有子块有行**。父块在这张表里**没有记录**（决策 ①）
- 主键 `(chunk_id, model)` → 天然支持同维度多模型并存（bge 与 embedding-3 都是 1024 维）
- 用 `halfvec` 而非 `vector` → 存储和索引都省一半，精度损失可忽略

### 2.6 `jobs` — 导入状态机

```sql
CREATE TABLE jobs (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT      NOT NULL DEFAULT 1,
    kind        TEXT        NOT NULL,               -- import|reindex
    payload     JSONB       NOT NULL DEFAULT '{}',  -- {"source_id": 123}
    dedupe_key  TEXT        NOT NULL,               -- 'import:123' → 幂等
    status      TEXT        NOT NULL DEFAULT 'queued', -- queued|running|done|failed|dead
    attempts    INT         NOT NULL DEFAULT 0,
    last_error  TEXT,
    locked_by   TEXT,                               -- worker 标识
    locked_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_dedupe_uq UNIQUE (dedupe_key)
);
```

**要点**：
- `dedupe_key UNIQUE` → 重复点"导入"不会排两个任务
- `locked_by + locked_at` → 支持乐观锁，worker 崩了任务可被回收
- `status='dead'` = 重试耗尽进死信，需要人工介入

---

## 3. 五个关键决定及理由

### ① `embeddings` 独立表，不塞进 `chunks`

**理由**：换 embedding 模型时，新旧可以**并存**——新表重建完再切换，旧的留着回滚。
塞进 `chunks` 就只能原地覆盖，**炸了没退路**。

**代价**：检索时多一次 join。可忽略（都在同一个库、同一次查询）。

### ② `sources` 1 : N `notes`

**理由**：一个文件在不同情况下产出不同数量的笔记：
- `reject` → 0 篇
- 正常 → 1 篇
- 大课件拆成"消息队列"和"RabbitMQ 配置" → N 篇
- 手写笔记 / 以后 Agent 生成 → `source_id = NULL`

### ③ `pending_ocr` 属于 `sources.parse_status`，**不是** `review_runs.action`

**理由**："待 OCR"是**文件级状态**（整个文件都没有文本层），不是某一次判定的结论。

**这同时修掉了一个映射冲突**：评测线发现 `reject + extract_spans` 是非法组合，但扫描件确实需要"排队等 OCR"这个动作。
把它放到 `parse_status` 后，`action` 保持干净的 4 个值：

```
action ∈ { index, extract_spans, topic_dialog, drop }
          ↑ accept   ↑ partial      ↑ reject      ↑ reject
```

### ④ **不加** `note_versions`

**理由**：v1 没有编辑功能。重新判定 = 覆盖 `notes.doc`，`updated_at` 记一笔就够。

**何时该加**：接 TipTap 编辑器时（v2）。

### ⑤ `notes.status` 做软删除，不真删

**理由**：真删一次要级联动 `notes` → `chunks` → `embeddings` 三张表。
软删除（`archived`）+ 检索时过滤 `status='active'` + 定时清理，最省事也最安全。

**代价**：需要所有检索查询都带上 `status='active'`（用 `Repo` 薄封装统一处理，别让每处自己写）。

---

## 4. 已知限制（必须承认）

### 4.1 pgvector 的维度是**列定义里写死的**

`vec halfvec(1024)` 里的 1024 不能动态变。所以：

- **同维度换模型**（bge-large ↔ embedding-3，都是 1024）→ 直接共用这张表，靠 `model` 字段区分
- **换维度的模型**（如 768 维）→ **不是改配置，是新建表**

**约定**：一维度一张表，命名 `embeddings_<dim>`。换模型流程：

```
建新表 → 后台分批重建 → 灰度切换读路径 → 验证 → 删旧表
```

绝不原地覆盖。

### 4.2 中文全文检索只能用 `pg_trgm`

PG 自带 FTS 不分中文词，`zhparser` 在 Windows 上编译麻烦。所以词法检索靠 `pg_trgm` 的二元/三元组匹配——**召回够用，但不如专业分词器**。

不满意时的旁路方案：挂 Meilisearch 做**索引**（真源仍是 PG，索引坏了重建）。

### 4.3 单用户假设写进了 `owner_id DEFAULT 1`

所有表都有 `owner_id`，但全是常量 1。**这是刻意的**：现在加字段零成本，以后加要动所有查询。

---

## 5. 索引设计

```sql
-- sources：去重 + 列表 + 失败排查
UNIQUE (owner_id, sha256)                       -- 由建表约束提供
CREATE INDEX ON sources (owner_id, created_at DESC);
CREATE INDEX ON sources (parse_status) WHERE parse_status <> 'ok';

-- review_runs：按文件溯查、按时间统计
CREATE INDEX ON review_runs (source_id, gate);
CREATE INDEX ON review_runs (created_at DESC);

-- notes：列表 + 溯源
CREATE INDEX ON notes (owner_id, status);
CREATE INDEX ON notes (source_id);

-- chunks：按笔记取块、按父块取子块
CREATE INDEX ON chunks (note_id, ord);
CREATE INDEX ON chunks (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX ON chunks (is_parent);

-- ★ 混合检索的词法一侧（中文靠 trgm 二元/三元组）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX chunks_text_trgm_idx ON chunks USING gin (text gin_trgm_ops);

-- embeddings：向量索引（余弦距离，halfvec）
CREATE INDEX embeddings_vec_hnsw ON embeddings
    USING hnsw (vec halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- jobs：并发安全
UNIQUE (dedupe_key)                             -- 由建表约束提供
CREATE INDEX ON jobs (status, locked_at);
```

> `m=16, ef_construction=64` 是 pgvector 的默认值。2.5 万块这个量级够用；
> 调优见 `docs/adr/` 后续记录，不必现在动。

---

## 6. 给实现者的硬要求

1. **迁移必须走 Alembic**，不许手改数据库。基线迁移 `0001_baseline.py` 已存在（空），新迁移挂它之后。
2. **`CREATE EXTENSION vector` / `pg_trgm` 要写进迁移**（`op.execute("CREATE EXTENSION IF NOT EXISTS ...")`）。
3. **所有查询走 `Repo(owner_id)` 薄封装**，不许裸用 session 拼 SQL。这样以后开 RLS 只改一处。
4. **禁止模块级可变全局状态**（`current_user`、`current_note` 一律靠参数传）。
5. 枚举值用 **TEXT + CHECK 约束**，不用 PG 原生 ENUM（原生 ENUM 加值要 ALTER TYPE，麻烦）。
6. `chunks` 要加 CHECK 保证 `(parent_id IS NULL) = is_parent`。
7. 写完后跑一次 `SELECT extversion FROM pg_extension WHERE extname='vector';` 记下版本。

---

## 7. 关系图（文字版）

```
sources ──1:N──> review_runs      (一个文件被三道闸各判一次)
   │
   └──1:N──> notes               (source_id 可空)
                │
                └──1:N──> chunks  (自引用：parent_id)
                             │
                             └──1:N──> embeddings   (只有子块有行)

jobs 独立存在，payload 里放 source_id
```

**删除路径**：删 `source` → `review_runs` 跟着删（CASCADE），`notes.source_id` 置 NULL（不删笔记）。
删 `note` → `chunks` → `embeddings` 级联删干净。**这就是"单库单事务"的价值。**

---

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-28 | 初版：6 张表 + 5 个关键决定 + pgvector 维度限制 + 索引设计 |
