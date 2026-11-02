# API 契约 v3

> **这份文件是前后端并行的唯一接口依据。** 前端按它写调用，后端按它实现，任何一方要改先找协调方。
> 变更要改版本号并写进文末「变更记录」，不许悄悄改字段名。
>
> 归属：**协调方**（见 `HANDOFF.md` §0.3）。后端/前端只能读，不能改。

约定：

- 前缀 `/api`，`GET /healthz` 例外（不带前缀，历史原因，别改）。
- 请求/响应一律 `application/json; charset=utf-8`；上传例外（`multipart/form-data`）。
- **多用户**：所有 `/api/*` 业务接口都要 `Authorization: Bearer <token>`（`/healthz` 与 `/api/auth/*` 除外）。
  数据按 `owner_id` 隔离；`owner_id` 由 token 解出，不再是常量（见 §9）。
- 时间一律 ISO-8601 带时区，例：`2026-10-29T01:20:00+08:00`。
- 错误一律 `{"detail": "<人能看懂的中文>"}` + 恰当的 4xx/5xx（FastAPI 默认形状，前端直接用）。
- 分页参数统一 `limit`（默认 20，上限 100）、`offset`（默认 0）；列表响应统一带 `total`。

---

## 0. 枚举取值（**唯一真源是数据库 CHECK 约束**，不是本文档）

> ⚠️ 下面这张表是 `docker compose exec db psql -U baleen -d baleen -c "\d+ <表>"`
> 抄下来的，**不是凭记忆写的**。前端按它做穷尽判断，后端不许返回表外的值。
> 要加新值 → 先加 Alembic migration，再回来更新这张表。

| 字段 | 表 | 全部可能值 |
|---|---|---|
| `parse_status` | `sources` | `pending` · `ok` · `no_text_expected` · `unsupported_format` · `error` · `pending_ocr` |
| `job.status` | `jobs` | `queued` · `running` · `done` · `failed` · `dead` |
| `job.kind` | `jobs` | `import` · `reindex` |
| `gate` | `review_runs` | `G0` · `G1` · `G2` |
| `verdict` | `review_runs` | `accept` · `partial` · `reject` · `skip` |
| `quality` | `review_runs` · `notes` | `core` · `normal` · `reference` · `junk`（可空） |
| `action` | `review_runs` | `index` · `extract_spans` · `topic_dialog` · `drop`（可空） |
| `status` | `notes` | `active` · `archived` |
| `lang` | `notes` | `zh` · `en` · `mixed`（可空） |
| `role` | `qa_messages` | `user` · `assistant` |

**说明**：
- `parse_status = pending` 表示**已上传、还没轮到 worker 解析**（瞬态）；`error` 是**解析失败**（不是 `failed`）；`pending_ocr` 表示等着走 OCR，前端画成「排队中」别画成错误。
- `job.status = dead` 是**重试耗尽放弃**（不是 `cancelled`，系统里没有"取消"这个状态）。
- `quality` 的四个值里，**真实数据现在全是 `core`**（G1/G2 的实际口径见 `service/gate/`）。
- `ext` **带点**：`.docx` / `.pdf` / `.md`（存的是 `Path().suffix`），前端别自己 strip。
- `sources` 与 `notes` 是 1:N，列表里的 `note_id` 只是**代表值**（取 id 最小的那篇）。

---

## 1. 健康检查（已实现）

### `GET /healthz`

```json
{ "status": "ok", "db": "ok", "redis": "ok" }
```

`status` 只有 `ok` / `error`。前端用它判断「后端在不在」，不要用它判断能不能检索。

---

## 2. 导入（已实现）

### `POST /api/sources`

`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | file | ✅ | 单个文件，≤ 50 MB |
| `no_llm` | bool（query） | ❌ | `true` 时跳过 G2（不耗额度），结果打 `g2_skipped=true` |

```json
{ "source_id": 12, "job_id": 34, "job_status": "queued", "deduped": false }
```

- `deduped=true` 表示同 `sha256` 已存在且已处理完，**不会再入队**，直接拿已有 `source_id`。
- **支持格式**：.pdf / .docx / .pptx / .md / .markdown / .txt / **.csv（v14 新增：整篇表格，首行当表头，
  自动嗅探分隔符，GB18030 兼容）** / 图片（v13 起走视觉转写）；.doc / .xlsx / .xls 显式拒绝并给转换提示。
- 415 = 扩展名不认识；413 = 超 50 MB；422 = 没带文件。
- `no_llm=false` 且该账号没配 API Key → **400**（提示去设置页，或改勾「跳过 AI 判定」）。

**导入时的标题结构二次加工**（`no_llm=false` 才跑）：
流水线在建笔记之前让 LLM 规划标题结构，补上缺失标题、给章节加编号
（`一、` / `2.1 ` / `2.1.3 `）、消除同级重名。**只动标题，正文不改**。
跳过条件：正文 < 400 字 / 已有 ≥2 个标题且无重名无空章且 80% 带编号。
失败静默降级（原结构保留，导入照常成功）。

> ⚠️ **需要非推理模型**。推理模型（如 sensenova）会把 `max_tokens` 全用在 reasoning 上、
> `content` 返回空，导致整理静默失败。实测 deepseek-flash 正常。

### `GET /api/sources/{source_id}`

> **图片文件（ADR 0006，2026-11-01 v13）**：配置了 LLM 后，图片(jpg/png/gif/webp/bmp/tif)导入时
> 由该用户的视觉模型转写为 Markdown 正文 → 正常过闸入库（`parse_status=ok`，`warnings` 带
> 「AI 视觉识别转写,可能有误差」）。没配 key / `no_llm` / 识别失败 → `no_text_expected`（旧语义）。
> 转写模型用推理型网关时可能返回空转写而被 G1 拒——换非推理视觉模型。（已实现）
>
> **扫描件 PDF（ADR 0006 §6，2026-11-02 v15）**：无文本层的 PDF（扫描件/纯图 PDF）在配置了 LLM 时
> **逐页栅格化(150dpi)走视觉转写**，页间以普通段落 `—— 第 N 页 ——` 分隔（不是标题，避免污染
> 笔记标题与目录）。单页失败只跳过该页并写进 `warnings`/`meta.failed_pages`；全部页失败才降级
> `no_text_expected`。页数上限 **30 页**（`PDF_VISION_MAX_PAGES`），超出部分**明确警告不静默截断**。
> 有文本层的 PDF 永远走 pymupdf 正常解析，不烧视觉调用。`meta.engine=pdf-vision(<模型>)`。

前端**轮询这一个接口**即可拿到全链路状态。

```json
{
  "id": 12,
  "filename": "读书笔记.docx",
  "ext": ".docx",
  "size_bytes": 24618,
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "parse_status": "ok",
  "message": null,
  "warnings": [],
  "g2_skipped": false,
      "headings_rewritten": true,
  "note_id": 7,
  "created_at": "2026-10-29T01:20:00+08:00",
  "job": { "id": 34, "status": "done", "attempts": 1, "last_error": null },
  "review_runs": [
    { "gate": "G0", "verdict": "accept", "quality": null, "note_type": null,
      "score": null, "action": null, "reasons": [], "model": null,
      "cost_tokens": 0, "latency_ms": 0 },
    { "gate": "G1", "verdict": "accept", "quality": "core", "note_type": null,
      "score": 0.72, "action": null, "reasons": ["含现象-原因-解决结构"],
      "model": null, "cost_tokens": 0, "latency_ms": 1 },
    { "gate": "G2", "verdict": "accept", "quality": "normal", "note_type": "reference",
      "score": null, "action": "index", "reasons": ["可复用知识"],
      "model": "glm-5.2", "cost_tokens": 1113, "latency_ms": 3400 }
  ]
}
```

**取值一律看 §0 的枚举表**（那里是数据库 CHECK 抄下来的）。补充三点：
- `parse_status = error` 是解析失败；`pending_ocr` 是等 OCR，**画成「排队中」不要画成错误**。
- `job.status = dead` 是重试耗尽放弃；系统里**没有**「已取消」这个状态。
- **`parse_status` 只在 `job.status = done` 时有意义。** 2026-10-31（0009 迁移）之前，
  刚上传还没被 worker 处理时它是 `error` 占位（`message=null`、`parse_meta={}` 的空壳）；现在这段窗口是 `pending`。
  **前端一律先看 `job.status`**：不是 `done` 就显示「排队中 / 处理中」，不要去解读 `parse_status`。

**轮询建议**：`job.status` 是 `queued`/`running` 时每 1 秒拉一次；到 `done`/`failed`/`dead` 就停。别无限轮询。

---

## 3. 列表与详情（**已实现 · 线 M**）

### `GET /api/sources`

| query | 默认 | 说明 |
|---|---|---|
| `limit` / `offset` | 20 / 0 | `limit` 上限 100 |
| `parse_status` | — | 过滤，可省略 |

```json
{
  "total": 35,
  "items": [
    { "id": 12, "filename": "读书笔记.docx", "ext": ".docx", "size_bytes": 24618,
      "parse_status": "ok", "message": null, "note_id": 7,
      "created_at": "2026-10-29T01:20:00+08:00" }
  ]
}
```

按 `created_at DESC`。**不含** `review_runs`（列表别带重字段，要看判定就拉单条）。

### `GET /api/notes`

| query | 默认 | 说明 |
|---|---|---|
| `limit` / `offset` | 20 / 0 | `limit` 上限 100 |
| `q` | — | 标题模糊匹配（`ILIKE`），不走向量；`%` `_` 已转义 |

```json
{
  "total": 9,
  "items": [
    { "id": 7, "title": "读书笔记", "lang": null, "tags": [],
      "quality": "core", "status": "active", "source_id": 12,
      "chunk_count": 18, "created_at": "2026-10-29T01:20:00+08:00" }
  ]
}
```

按 `created_at DESC`。`chunk_count` 是**父块数**（section 数），不是子块。

> **`lang` / `tags` 现在实测是 `null` / `[]`** —— 导入侧还没做语言识别和打标签，不是接口的锅。
> 前端别拿 `lang === 'zh'` 做分支判断。

### `GET /api/notes/{note_id}`

```json
{
  "id": 7, "title": "读书笔记", "lang": null, "tags": [],
  "quality": "core", "status": "active", "source_id": 12,
  "markdown": "# 第一章 概述

父块全文……",
  "parents": [
    { "chunk_id": 55, "ord": 0, "heading_path": "第一章 > 概述",
      "text": "父块全文……", "token_count": 320 }
  ]
}
```

`parents` 按 `ord ASC`，只含 `is_parent=true` 的块。**详情页只渲染父块**，
子块是检索用的中间产物，前端不展示（要看就展开「为什么命中」，用检索接口的 `child_text`）。

> **`markdown`（2026-10-31 v11 起有值）**：由 `notes.doc` **现场序列化**的 markdown
> （编辑器工作稿，见 `PUT /api/notes/{note_id}`）。笔记真源仍是 block JSON（ADR 0001）。
> 数据库的 `notes.markdown` 列**已于 0010 迁移删除**（从未生成过的死列，主人拍板删列，
> 以后真有用再加）；API 的 markdown 字段与该列无关。doc 为空的笔记该字段是空串。

**404 = 没这条笔记**。`status = 'archived'` 的笔记按软删除语义也返回 404（不区分「不存在」和「已归档」）。

### `PUT /api/notes/{note_id}`（用户自编辑，2026-10-31 v11）

```json
// 请求体:两个都可选,至少给一个;同内容重复保存是 no-op(不重嵌)
{ "title": "新标题", "markdown": "# 新结构

正文…" }

// 响应
{ "id": 7, "title": "新标题", "title_changed": true, "content_changed": true,
  "chunks_parents": 2, "chunks_children": 3, "embeddings": 3 }
```

- **不跑 LLM**：用户写什么就是什么，不做标题二次加工；回填幂等闸（`parse_meta.headings_rewritten`）保持原值，
  后续回填不会覆写用户 edits；`parse_meta.user_edited=true` 标记
- **内容编辑 = 换 doc + 删光 chunks 重建 + 重嵌**（同一事务，失败整体回滚，与线 R 回填同配方）
- `400` = 笔记含**图片块**时改正文（markdown 往返会把图压成 alt 文本，静默丢块 → 显式拒绝，可只改标题）
- `404` = 没这条笔记；`422` = 两个都缺 / title 或 markdown 空白
- 首次调用会加载嵌入模型（bge，冷启动数秒），之后热态毫秒级

### `DELETE /api/notes/{note_id}`（删除笔记，2026-11-02 v14）

- **真删除，不是软删**：删 note 行，chunks/embeddings 由 DB 外键级联一并清空（检索立刻不再命中）；
- **source 连带**：该笔记的 source 行在没有其他笔记引用时一并删除，原文件 blob 从磁盘清除
  （best-effort，失败只记日志）；`sources:notes = 1:N`，还有别的笔记挂着就只删笔记；
- `204` = 删成；`404` = 没这条笔记（owner 隔离别人的也是 404，与详情口径一致）；
- 问答消息里引用过这篇笔记的角标此后点开是 404，引用文字本身不受影响。

---

## 4. 检索（**已实现 · 线 M**）

### `POST /api/search`

```json
{ "query": "内存泄漏怎么排查", "top_k": 5 }
```

- `query` 1–500 字；**纯空白 → 422**（不是静默默认值）。
- `top_k` 默认 5，上限 20。
- **回答兜底协议（`[NO_KB]`，内部机制）**：检索片段答不了时 LLM 输出 `[NO_KB]` 标记，
  端点检测到后**自动联网二次回答**（带 `[W n]` 出处）；未配 Tavily 则用 AI 自身知识
  正面回答并在开头标注「以下用 AI 自身的知识回答」。笔记库能答时仍严格只用库内内容。
- **`note_ids`（引用笔记，可选）**：用户 @ 某篇笔记 = 告诉 AI「在问什么」——
  正文以 `<referenced_notes>` 进 prompt 优先回答，**检索照跑**找关联笔记；
  答案里引用内容标 `[引n]`、检索片段标 `[n]`。非法/别人的 id 静默忽略；最多 5 篇，单篇截 8000 字。

响应（**命中单位是父块**，small-to-big：小块检索、父块出）：

```json
{
  "query": "内存泄漏怎么排查",
  "took_ms": 78,
  "hits": [
    {
      "parent_id": 55,
      "parent_text": "父块全文……",
      "heading_path": "第一章 > 概述",
      "note_id": 7,
      "note_title": "读书笔记",
      "rrf_score": 0.0328,
      "child_id": 61,
      "child_text": "命中的那个子块……",
      "vector_rank": 1,
      "lexical_rank": 1
    }
  ]
}
```

前端要知道的**四**件事（答辩会问）：

1. **`rrf_score` 没有绝对意义**，公式 `Σ 1/(60+名次)`，只用来排相对高低。两路都排第 1 时 = `2/61 ≈ 0.0328`。
2. `vector_rank` / `lexical_rank` 为 `null` 表示该路没召回到这个块。**两路都命中 = 最可信**。
3. `hits` 为空数组是**正常结果**（库里没相关内容），不是错误。前端显示「没找到」而不是报错。
4. **`took_ms` 是这次请求的墙钟，冷启动那一次会明显偏大。** 实测热值 `119–134ms`，
   首次含 bge 模型加载是 `3061ms`；而**第一个检索请求的整体墙钟约 17.5s**
   （torch / sentence-transformers 首次 import 不在计时窗口内）。

> **⚠️ 已知取舍：检索没有相关性阈值。**
> 向量路没有距离下限，随手输一串库里肯定没有的词，照样会返回 `top_k` 条近邻。
> 所以「`hits` 为空」在真实链路上几乎不会出现。
>
> **为什么不做阈值**：RRF 分数是「名次」的函数，**没有绝对量纲**，定一个绝对阈值是无意义的；
> 定相对阈值（如 `top1 × 0.5`）能拍脑袋拍出来，但会真实地丢召回。
> v1 的选择是**不藏**：把两路名次一起返回，让界面自己去表达可信度。
> 答辩若被问「怎么判断没找到」，答：**看两路名次，不看分数绝对值**。
>
> 这条要改的话，正确做法是给 `vector_search` 加**余弦距离上限**并把「没找到」单独成一个状态，
> 属于下一批，不在 v1 范围内。

---

## 5. 问答与会话（**已实现 · 线 Q**）

聊天式多轮问答：会话落库（`qa_sessions`/`qa_messages`），追问先经 LLM 改写检索词（best-effort，失败回退原话），回答基于库内检索片段 + 对话历史，**不编造库外内容**。出处只保留答案真正引用的 `[n]`。

### `POST /api/qa/chat`（非流式）

```json
{ "session_id": 9, "question": "它有哪几种状态", "top_k": 5 }
```

- `session_id` 缺省/null = 自动建会话（title = 首问前 30 字）
- 追问先经 LLM 改写检索词；`search_query` 透出实际使用的检索词
- 原子落库 user/assistant 两条消息；user 消息另存 `llm_content`（含检索上下文，多轮前缀对齐用）

```json
{
  "session_id": 9, "session_title": "闭包是什么",
  "user_message": { "id": 1, "role": "user", "content": "…", "citations": [], "model": null, "cost_tokens": 0, "cache_hit_rate": null },
  "assistant_message": { "id": 2, "role": "assistant", "content": "…[1]", "citations": [{ "n": 1, "note_id": 156, "note_title": "…", "heading_path": "…", "snippet": "…" }], "model": "glm-5.2", "cost_tokens": 2169, "cache_hit_rate": 0.4843 },
  "search_query": "Promise 有哪几种状态", "took_ms": 7115
}
```

### `POST /api/qa/chat/stream`（SSE 流式，同请求体）

响应 `text/event-stream`，事件序：`meta` → `delta`* → `done`；LLM 失败 → `error`（用户消息已落库）。

- `meta`：`{session_id, session_title, search_query, user_message}`
- `delta`：`{text}`（正文增量；推理模型的思考过程不下发）
- `done`：`{assistant_message, took_ms}`（完整消息含出处与 cache_hit_rate，已落库）

### 会话管理

| 端点 | 说明 |
|---|---|
| `GET /api/qa/sessions?limit=50` | 会话列表（最新在前），`{total, items:[{id,title,created_at}]}` |
| `GET /api/qa/sessions/{id}` | 重开会话，`{id,title,created_at,messages:[Message]}` |
| `DELETE /api/qa/sessions/{id}` | 删会话（消息 CASCADE），204 |
| `GET /api/qa/sessions/{id}/export` | 导出 Markdown（浏览器下载；同时在 `tmp/qa_exports/` 落一份） |

**`cache_hit_rate` 口径**：prompt 前缀缓存命中占比 = hit/(hit+miss)；上游未命中时不下发字段 → `null`（前端显示「—」）。字段双格式归一：DeepSeek `prompt_cache_hit_tokens` / OpenAI `prompt_tokens_details.cached_tokens`。

**联网兜底**：库内未命中且设置页配了 Tavily key → 自动搜网，回答基于搜索结果、citations 带 `url`（`note_id=null`，前端渲染外链）、`used_web=true`；未配 key 或搜索失败 → 维持「没找到」口径。

### `POST /api/qa/sessions/{id}/note-plan`（阶段4 增强）

存笔记前的**主题分析**：跨主题对话让用户先选「拆几篇/要哪篇」。

```json
{
  "topics": [
    { "id": 0, "title": "数据库索引", "summary": "B+ 树结构", "msg_start": 0, "msg_end": 3 },
    { "id": 1, "title": "SSD 硬件", "summary": "选购要点", "msg_start": 4, "msg_end": 7 }
  ],
  "suggestion": "split"
}
```

- `suggestion`：`split`（主题间基本无关，建议拆）/ `single`（同一主题，建议一篇）
- 会话消息 <2 → `400`；规划失败自动回退单主题（不报错）

### `POST /api/qa/sessions/{id}/save-note`（阶段4）

会话整理成结构化 Markdown：LLM 组织 → 落 `notes_out/` 文件 + **no_llm 入库**（跳过 G2，走 G0/G1/切块/向量化，可检索）。响应 `{note_file, organized_chars, import_enqueued, import_job_id}`。会话不足 2 条消息 → 400。

---

## 6. 设置（**已实现 · 线 Q**）

模型与网关配置存 `app_settings` 单行（字段 NULL 回退 .env 对应项），LLM 调用前现读 → **保存后热生效**（无需重启）。api_key 只写不读。

### `GET /api/settings`

```json
{ "llm_base_url": "http://…", "llm_model": "glm-5.2", "api_key_set": true, "api_key_tail": "abcd" }
```

### `PUT /api/settings`

```json
{ "llm_base_url": "…", "llm_model": "…", "llm_api_key": "…" }
```

- 三字段均可省略（= 保持不变）；`llm_api_key` 传空串 = 保持不变，传新值 = 覆盖
- 已知边界：worker 的 LLM 配置暂仍读 .env（设置页只影响问答/改写链路）；charter 已改为 DB 优先（见 §8）

---

## 7. 明确不做（前端别要，后端别加）

| 不做 | 原因 |
|---|---|
| 邮箱验证 / 找回密码 / 第三方登录 | 只做用户名+密码；毕设不需要更复杂的流程 |
| 删除账号 | 演示不需要；误删事故风险 > 收益（笔记/会话的删除均已实现） |
| 分页以外的复杂搜索（排序、过滤组合） | 20 个 query 的评测还没出来，先别过度设计 |

---

## 8. Charter 章程（**已实现**）

**每个账号各自一份章程**（`app_settings` 每用户一行）。首次登录弹问卷向导 → LLM 生成章程 → 存该用户行 → G2 热生效。
DB charter 优先于 `charter.md` 文件（`resolve_charter`：DB → 文件 → DEFAULT_CHARTER）。

### `GET /api/charter`

```json
{ "has_charter": false, "charter": null }
```

`has_charter=true` 表示**当前账号**已有章程；`false` = 回退 `charter.md` 文件。前端用它判断要不要弹问卷。

`PUT /api/charter` 传空串 = 清空该账号的章程（回退文件）。

### `POST /api/charter/generate`

问卷答案 → LLM 生成章程文本（**不落库**，前端预览编辑后再 PUT）。

```json
{
  "direction": "软件工程本科生",
  "purpose": "课设、答辩",
  "collect": ["讲清概念或方法的笔记（定义+例子）", "技术文档、论文、项目说明"],
  "reject": ["单据与行政表格", "空文件、hello world、测试文件"],
  "unsure": "collect_low",
  "extra": "关注前端技术栈"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `direction` | string | 专业方向 / 身份 |
| `purpose` | string | 笔记库用途 |
| `collect` | string[] | 想收什么（多选） |
| `reject` | string[] | 不收什么（多选） |
| `unsure` | string | `collect_low`（收下标低质量）/ `reject`（拒掉）/ `discuss`（提出来聊） |
| `extra` | string | 补充说明（可选） |
| `domains` | string[] | 技术领域多选（扩展问卷，可选） |
| `sources` | string[] | 笔记来源偏好 |
| `formats` | string[] | 喜欢的笔记形态 |
| `quality_marks` | string[] | 好笔记的标准 |

```json
{ "charter": "## 这是谁的库\n我是一名软件工程本科生……" }
```

### `PUT /api/charter`

保存章程到 DB（热生效：G2 下次调用即从 DB 读到新章程）。

```json
{ "charter": "## 这是谁的库\n我是一名软件工程本科生……" }
```

- 返回同 `GET` 的 `{ has_charter, charter }`
- 空串 = 清空（回退 `charter.md` 文件）

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-02 | v15 | **扫描件 PDF 视觉识别（ADR 0006 §6）**：无文本层 PDF 在有 LLM 配置时逐页栅格化(150dpi)走视觉转写，页间 `—— 第 N 页 ——` 普通段落分隔；单页失败跳过留痕、全失败降级 `no_text_expected`；页数上限 30 明说警告；有文本层的 PDF 不受影响（不烧视觉）。`meta.engine=pdf-vision(<模型>)` |
| 2026-11-02 | v14 | **三小批**：§3 新增 **`DELETE /api/notes/{note_id}`**（真删除：chunks/embeddings DB 级联清，孤儿 source 连行带 blob 一起删，204/404）；导入格式加 **`.csv`**（整篇表格、首行表头、分隔符嗅探、GB18030 兼容——xlsx 提示里「另存为 CSV」从此是真的）；问答 FORMAT_GUIDE 与存为笔记 prompt 支持 **```svg 插图**（前端沙箱 iframe 渲染，`sandbox` 无 allow-* + CSP `default-src 'none'`，解析失败退化代码块）；§7 删掉已过时的「站内编辑笔记不做」行 |
| 2026-11-01 | v13 | **图片视觉理解（ADR 0006）**：图片文件导入时由用户配置的视觉模型转写为 Markdown 正文（失败降级 `no_text_expected`，`warnings` 带「AI 视觉识别转写」提示）；问答命中图片来源的笔记时**回传原图**给模型（最多 2 张），流式 meta 增加 **`images_attached`**；非流式 chat 响应不变 |
| 2026-10-31 | v12 | **0010 迁移：删除 `notes.markdown` 死列**（从未生成过，API 字段由 doc 现场序列化不受影响）；后端启动后台线程**预热嵌入模型**（消除重启后第一问冷启动，`EMBED_WARMUP_ON_STARTUP=false` 可关） |
| 2026-10-31 | v11 | §3 新增 **`PUT /api/notes/{note_id}` 用户自编辑**（标题/markdown；内容编辑重建切块+向量，同事务回滚；图片块禁改正文）；`GET /api/notes/{id}` 的 `markdown` 改为 **doc 现场序列化**（不再恒 null） |
| 2026-10-31 | v10 | §0 `parse_status` 枚举加 **`pending`**（0009 迁移）：新上传的 source 在 worker 解析前不再是 `error` 占位；切块器代码块超 480 token 按行切分（不再静默截断，超限分片带 `code_part`） |
| 2026-10-31 | v9 | §5 问答加 **`[NO_KB]` 回答兜底协议**（KB 答不了→联网/AI 自身知识，不再拒答）；问答页会话 id 同步到 URL（`/qa?session=N`）；mermaid 图点击灯箱放大（滚轮缩放）与窄容器文字裁切修复 |
| 2026-10-31 | v8 | chat 请求加 **`note_ids` 引用笔记**（`<referenced_notes>` 优先上下文 + 检索照跑）；**前端收敛为会话优先**——导入/检索并入对话（`/导入` 文件选择器、`/检索` 引用选择器），删除 `/import` `/search` 路由页，导航只剩 笔记库/问答/设置 |
| 2026-10-30 | v7 | §5 新增 **note-plan**（存笔记前主题分析）与 save-note 请求体（mode/topic_ids/instruction 多篇生成，响应加 `notes[]`）；§8 charter 答案扩展 `domains/sources/formats/quality_marks`（向后兼容） |
| 2026-10-30 | v6 | §2 新增**导入时标题结构二次加工**（LLM 规划 + 代码算编号；需非推理模型）；source 响应加 `headings_rewritten`；上传补 400（没配 API Key）
| 2026-10-30 | v5 | 新增 **§9 认证**（注册/登录/me + 数据隔离）；**全站改为多用户**——所有业务接口需 `Authorization: Bearer`，`owner_id` 由 token 解出；`app_settings` 改每用户一行（章程随行归属），API Key 不再回退 `.env`；§7 移除「登录/注册」 |
| 2026-10-30 | v4 | 新增 **§8 Charter 章程**（GET/POST generate/PUT）；worker 的 charter 改为 DB 优先（`resolve_charter`），§6 已知边界更新 |
| 2026-10-29 | v1 | 首版：把已实现的 `/healthz`、`POST /api/sources`、`GET /api/sources/{id}` 固化，并定下待实现的 4 个查询接口，供线 L（前端）与线 M（后端）并行开发 |
| 2026-10-29 | v2 | 线 M / 线 L 交付后按**真实返回**订正，补齐 §0 枚举表。**v1 凭记忆写的枚举有 6 处与数据库 CHECK 不符**：`verdict` 写 `pass`（实为 `accept/partial/reject/skip`）、`quality` 写 `high`（实为 `core/normal/reference/junk`）、`action` 写 `accept`（实为 `index/extract_spans/topic_dialog/drop`）、`parse_status` 写 `failed`（实为 `error`）且漏了 `pending_ocr`、`job.status` 写 `cancelled`（实为 `dead`）、`ext` 写成无点。另补：`markdown`/`lang` 实测为 `null`、`parse_status` 只在 `job.status=done` 时有意义、`took_ms` 冷启动语义、检索无相关性阈值的取舍。**教训：契约里的枚举必须从数据库 CHECK 抄，不能凭记忆编。** |

---

## 9. 认证（**已实现**）

用户名 + 密码 + JWT。密码用标准库 `scrypt` 哈希（不存明文），token 有效期 7 天（`JWT_EXPIRE_MINUTES`）。

> **除 `/healthz` 与本节端点外，所有 `/api/*` 都要带 `Authorization: Bearer <token>`，否则 401。**

### `POST /api/auth/register`

开放注册。用户名 3-32 字符、密码 8-128 字符。

```json
{ "username": "alice", "password": "my-password" }
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "user": { "id": 1, "username": "alice" }
}
```

- 用户名已存在 → `409`「用户名已被占用」
- **第一个注册的账号拿到 `id=1`，继承项目单用户时期的全部存量数据**（笔记/会话/设置/章程）

### `POST /api/auth/login`

请求体同注册。用户不存在与密码错误统一返回 `401`「用户名或密码错误」（不泄露用户名是否注册过）。

### `GET /api/auth/me`

需要 token。返回 `{ "id": 1, "username": "alice" }`。

### 数据隔离

- 所有业务表查询按 token 解出的 `owner_id` 过滤；拿别人的 `note_id`/`session_id` 一律 404
- `app_settings` 每用户一行（唯一约束 `owner_id`）：LLM 网关/模型/API Key、Tavily Key、章程各自独立
- **API Key 不继承 `.env`**：新账号必须自己到设置页配（`api_key_set=false` 时上传会 400）
- 导入任务（worker）也按 `owner_id` 落库；`jobs` 去重键是 `(owner_id, dedupe_key)`
