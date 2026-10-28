# API 契约 v1

> **这份文件是前后端并行的唯一接口依据。** 前端按它写调用，后端按它实现，任何一方要改先找协调方。
> 变更要改版本号并写进文末「变更记录」，不许悄悄改字段名。
>
> 归属：**协调方**（见 `HANDOFF.md` §0.3）。后端/前端只能读，不能改。

约定：

- 前缀 `/api`，`GET /healthz` 例外（不带前缀，历史原因，别改）。
- 请求/响应一律 `application/json; charset=utf-8`；上传例外（`multipart/form-data`）。
- **单用户**：没有鉴权头，`owner_id` 后端写死 1（AGENTS §5 决策 11）。
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
| `parse_status` | `sources` | `ok` · `no_text_expected` · `unsupported_format` · `error` · `pending_ocr` |
| `job.status` | `jobs` | `queued` · `running` · `done` · `failed` · `dead` |
| `job.kind` | `jobs` | `import` · `reindex` |
| `gate` | `review_runs` | `G0` · `G1` · `G2` |
| `verdict` | `review_runs` | `accept` · `partial` · `reject` · `skip` |
| `quality` | `review_runs` · `notes` | `core` · `normal` · `reference` · `junk`（可空） |
| `action` | `review_runs` | `index` · `extract_spans` · `topic_dialog` · `drop`（可空） |
| `status` | `notes` | `active` · `archived` |
| `lang` | `notes` | `zh` · `en` · `mixed`（可空） |

**说明**：
- `parse_status = error` 是**解析失败**（不是 `failed`）；`pending_ocr` 表示等着走 OCR，前端画成「排队中」别画成错误。
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
- 415 = 扩展名不认识；413 = 超 50 MB；422 = 没带文件。

### `GET /api/sources/{source_id}`（已实现）

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
- **`parse_status` 只在 `job.status = done` 时有意义。** 刚上传、还没被 worker 处理时，
  它的值是不可信的（实测会出现 `error` 但 `message=null`、`parse_meta={}` 的空壳）。
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
  "markdown": null,
  "parents": [
    { "chunk_id": 55, "ord": 0, "heading_path": "第一章 > 概述",
      "text": "父块全文……", "token_count": 320 }
  ]
}
```

`parents` 按 `ord ASC`，只含 `is_parent=true` 的块。**详情页只渲染父块**，
子块是检索用的中间产物，前端不展示（要看就展开「为什么命中」，用检索接口的 `child_text`）。

> ⚠️ **`markdown` 现在实测全是 `null`**（类型是 `string | null`）。
> ADR 0001 决策 2 说「笔记真源 = block JSON，markdown 只是渲染」，
> 而导入侧目前只落了父块、没生成 markdown。
> **所以详情页的正文真源是 `parents[]`，不是 `markdown`** —— 前端必须两者都渲染，
> `markdown` 为 null 时退化成「按 `heading_path` + `text` 列父块」，不要显示空白页。

**404 = 没这条笔记**。`status = 'archived'` 的笔记按软删除语义也返回 404（不区分「不存在」和「已归档」）。

---

## 4. 检索（**已实现 · 线 M**）

### `POST /api/search`

```json
{ "query": "内存泄漏怎么排查", "top_k": 5 }
```

- `query` 1–500 字；**纯空白 → 422**（不是静默默认值）。
- `top_k` 默认 5，上限 20。

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

## 5. 明确不做（前端别要，后端别加）

| 不做 | 原因 |
|---|---|
| 登录 / 注册 / token | 单用户，AGENTS §8 |
| 站内编辑笔记（PUT/PATCH） | 真源是 block JSON，v2 再说 |
| 删除 | 单用户演示不需要；误删演示事故风险 > 收益 |
| 流式 SSE 回答 | 问答生成是下一批（需要 LLM 额度），先只做检索 |
| 分页以外的复杂搜索（排序、过滤组合） | 20 个 query 的评测还没出来，先别过度设计 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-29 | v1 | 首版：把已实现的 `/healthz`、`POST /api/sources`、`GET /api/sources/{id}` 固化，并定下待实现的 4 个查询接口，供线 L（前端）与线 M（后端）并行开发 |
| 2026-10-29 | v2 | 线 M / 线 L 交付后按**真实返回**订正，补齐 §0 枚举表。**v1 凭记忆写的枚举有 6 处与数据库 CHECK 不符**：`verdict` 写 `pass`（实为 `accept/partial/reject/skip`）、`quality` 写 `high`（实为 `core/normal/reference/junk`）、`action` 写 `accept`（实为 `index/extract_spans/topic_dialog/drop`）、`parse_status` 写 `failed`（实为 `error`）且漏了 `pending_ocr`、`job.status` 写 `cancelled`（实为 `dead`）、`ext` 写成无点。另补：`markdown`/`lang` 实测为 `null`、`parse_status` 只在 `job.status=done` 时有意义、`took_ms` 冷启动语义、检索无相关性阈值的取舍。**教训：契约里的枚举必须从数据库 CHECK 抄，不能凭记忆编。** |
