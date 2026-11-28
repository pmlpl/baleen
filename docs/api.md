# API 契约 v45

> **版本号以文末「变更记录」的最新一条为准**（2026-11-07 校正：头部曾长期停留在 v3）。
> 改任何字段都必须同时升这里和变更记录的版本号。
>
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

```json
{ "id": 12, "job_id": 34, "job_status": "queued", "deduped": false }
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `attachments` | file[] | ❌ | **v16**：md 里引用的本地图片，按**原相对路径**随主文件一起传（最多 40 个，仅图片扩展名） |

> **md 带图片导入（v16）**：md 里的 `![alt](相对路径)` 会解析到「本次上传目录」下的同名相对路径。
> 解析到的图片**按内容寻址存库**（`storage/media/{sha256}`），doc 里只留 `media` key；
> 详情页按 `GET /api/media/{key}` 取原图。文件不存在 → 退回替代文字并在 `warnings` 里说明；
> 外链（`http(s)://`）保留 `src` 引用、**不联网抓取**（G0 不做网络 IO）。
> 旧的单文件上传完全兼容（不传 `attachments` 即旧行为）。

- `deduped=true` 表示同 `sha256` 已存在且已处理完，**不会再入队**，直接拿已有 `source_id`。
- **支持格式**：.pdf / .docx / .pptx / .md / .markdown / **.html / .htm（v28 新增：剥 style/script，
  内嵌 `data:` 图片按内容寻址存库，外链不联网抓）** / .txt / **.csv（v14 新增：整篇表格，首行当表头，
  自动嗅探分隔符，GB18030 兼容）** / 图片（v13 起走视觉转写）；.doc / .xlsx / .xls 显式拒绝并给转换提示。
- 415 = 扩展名不认识；413 = 超 50 MB；422 = 没带文件。
- 该账号没配 API Key → **400**（所有内容必须经 AI 笔记整理，防止未整理内容污染知识库）。

**AI 笔记整理**（v21 起，替代 G2 归属闸 + 标题整理）：
流水线在建笔记之前让 LLM 读 `note-format.md` 契约，重写整篇 markdown——
补缺失标题、组织结构、去掉明显噪音（营销话术等），标题只写语义不写编号（编号系统自动加）。
如果全文无可整理的知识点（纯广告）→ 不入库（`G2 verdict=reject`）。
LLM 异常/输出不可解析 → 降级原文入库（不因整理失败丢内容）。

> ⚠️ **需要非推理模型**。推理模型会把 `max_tokens` 全用在 reasoning 上、`content` 返回空，导致整理降级。

### `POST /api/sources/url`（v20 新增，v23 whisper 改异步，v24 支持抖音）

B 站 / 抖音视频链接导入。两种模式：

- `mode="subtitle"`（默认）：同步拉取 B 站 CC/AI 字幕，拼成 markdown，走常规管线。秒级返回。**仅 B 站**。
- `mode="whisper"`（v23 改异步）：**立即返回 201**，worker 在后台下载音频 + whisper 转写 + 帧提取 → 写 markdown → 调 `run_import_async(source_id=...)` 入库。不阻塞 API。**支持 B 站和抖音**。

`application/json`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | string | ✅ | B 站视频链接，如 `https://www.bilibili.com/video/BV1GJ411x7h7` |
| `mode` | string | ❌ | `"subtitle"`（默认，秒级，同步） \| `"whisper"`（分钟级，异步，内容可靠） |

响应与 `POST /api/sources` 完全一致（`{ source_id, job_id, job_status, deduped }`）。
轮询 `GET /api/sources/{source_id}` 拿全链路状态。whisper 模式通过 `video_stage`（阶段文案）+ `video_progress`（0-100 进度）+ `video_est_seconds`（转写阶段预计秒数，前端据此插值）三个字段驱动进度条；另有 `video_log`（v27 新增）——提取+整理全程的白盒日志数组，每条 `{ts, stage, pct}`（时间戳/阶段文本/百分比），前端渲染滚动日志区，让用户看到「排队→下载→whisper→转写校正中→识别画面→解析完成→AI 整理」每一步走到哪了。**转写校正中**（v35 新增）：whisper 转写完成后、画面识别前，LLM 盲修疑似错听的专名/术语（spike 实测：零词典、只出修改清单、程序化应用留痕；输入 3 万字符封顶；LLM 失败自动降级用原转写不让导入失败），修正留痕写 `parse_meta.llm_transcript_fix`（`{n_proposed, n_applied, corrections[], n_missed, missed[]}`，失败/跳过记 `error`/`skipped`）。同时 whisper 模型 small→large-v3-turbo、语言参数 zh 强制→None 自动检测（英文视频不再被强解码，8093 专名错字 ≥20 的根源修复）抖音模式**免 cookie**：worker 用系统浏览器（Playwright，channel=msedge）打开视频页拦截详情接口获取元信息和播放地址，用户零配置。

**`video_cover_url`（v43 新增）**：视频封面，值是可直接当 `<img src>` 用的相对 URL（`/api/media/{key}`），null = 没有封面/非抖音/还没提取到。
抖音不支持网页内嵌播放，前端拿它渲染**海报卡**代替纯黑占位框（主人 2026-11-26 反馈「这是一个黑色的，不好看」——根因是抖音复用了 B 站 iframe 的 16:9 黑底容器）。
⚠️ 存的是**本项目 media 的内容寻址 key**，不是抖音原始地址：抖音封面是签名链（实测 `x-expires` 两周后过期），存原样会变裂图。
⚠️ 封面在**下载视频之前**就拿到并立即写库（`cover_cb`），不是等整条提取链结束——否则 40 分钟的视频要让海报卡白等十几分钟。

- `mode="whisper"` 时 API 立即返回 201，不阻塞；worker 后台提取（最多 2 小时，支持 ≤60 分钟视频）。
- whisper 模式的 source.sha256 是占位值（`"video:{bvid}:whisper"`），真实 sha 存在 `parse_meta.content_sha256`；dedupe 基于 BVID。
- 400 = URL 无法识别 BV 号 / 视频没有字幕（subtitle 模式）/ 没配 API Key / 视频超过 60 分钟。
- 502 = B 站接口请求失败（subtitle 模式）/ 视频提取失败（whisper 模式，worker 内部错误）。
- 字幕来源：B 站 player/v2 接口，带该用户的 SESSDATA（扫码登录落库，见下方「B 站扫码登录」；历史 `.env` 的 `BILIBILI_SESSDATA` 仅作回退）才能拿到大部分视频的 CC/AI 字幕；未配置时部分视频会报「没有字幕」。
- 同一视频重复导入 → BVID 去重（whisper 模式）或 sha256 去重（subtitle 模式）。

### B 站扫码登录（v30 新增，半自动获取 SESSDATA）

B 站对匿名自动访问做页面级 412 风控，浏览器兜底（Playwright 过挑战）需要登录态门票
SESSDATA。v30 起**不用手抄 cookie**：扫码登录后 Playwright 抓 SESSDATA 落 `app_settings`
（DB 热生效，全进程现读，无需重启），之后数月免登录、过期重扫。

| 端点 | 说明 |
|---|---|
| `POST /api/bilibili/login` | **弹真浏览器**（系统 Edge）打开 bilibili.com → 自动点出二维码 → 等用户扫码 → 抓 SESSDATA 落库。**同步阻塞**，最长约 3.5 分钟（180s 等扫码 + 前置导航，vite proxy 300s 装得下）。200 → `{logged_in: true, uid, logged_at}`；400 = 用户关窗取消 / 等待扫码超时；502 = 打不开页面 / 登录弹窗没出来（风控或结构变化） |
| `GET /api/bilibili/login` | 当前登录态 `{logged_in, uid, logged_at}`；未登录 `logged_in=false`。**SESSDATA 全文永不回显**（凭证只写不读） |

两个入口：
- **自动**（worker）：whisper 导入被 B 站风控拦截且未配置/已过期时，兜底触发扫码——`video_stage`/`video_log` 写「⏰ 请扫码登录 B 站（浏览器窗口已弹出，180 秒内扫码）」，扫码成功自动落库并原地重试兜底（导入无感继续）。扫码超时/取消 → job failed，报错文案给指引。
- **手动**（视频笔记页）：VideoView「B 站登录（扫码）」按钮（已登录显示 uid+上次扫码日期，变「重新扫码」）——提前扫好 / 超时后重扫 / 独立测试口。

⚠️ 红线：SESSDATA **只给 Playwright 浏览器上下文用**（HTTP 重放对 B 站风控无效——TLS 指纹打分，实测过）；既有消费者（兜底浏览器 / 字幕接口 player/v2 / yt-dlp 首选路径 cookie 文件）不变，变的只是值的来源（F12 手抄 → 浏览器扫码）。存储：迁移 0015 `app_settings` 加 `bilibili_sessdata` / `bilibili_sessdata_at` / `bilibili_uid` 三列，DB 优先、`.env` 回退（纯兼容）。

### `GET /api/sources/{source_id}`

> **图片文件（ADR 0006，2026-11-01 v13）**：配置了 LLM 后，图片(jpg/png/gif/webp/bmp/tif)导入时
> 由该用户的视觉模型转写为 Markdown 正文 → 正常过闸入库（`parse_status=ok`，`warnings` 带
> 「AI 视觉识别转写,可能有误差」）。没配 key / 识别失败 → `no_text_expected`（旧语义）。
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
  "video_stage": null,
  "video_progress": null,
  "video_est_seconds": null,
  "video_log": null,
  "video_cover_url": null,
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
- **含图笔记也能改正文（v16）**：图片在 markdown 里以位置标记 `![说明](baleen-image:N)` 出现，
  保存时按 `N` 重新关联编辑前的原图片节点（`N` 是编辑前文档里的图片序号，从 0 起）。
  用户挪动标记行图片跟着挪、删掉标记行图片即删除——都是显式意图，不算丢数据。
  越界或手写错的编号退化为替代文字不报错
- `404` = 没这条笔记；`422` = 两个都缺 / title 或 markdown 空白
- 首次调用会加载嵌入模型（bge，冷启动数秒），之后热态毫秒级

### 笔记删除与纸篓（**v37 改版：软删除**）

> **v37 语义变更**：`DELETE /api/notes/{id}` 从「真删除」改成「**移入纸篓**」（软删除）。
> 依据是 ADR 0003 §3⑤ 原本的设计（「软删除 `archived` + 检索过滤 `status='active'` +
> 定时清理」）—— 字段与过滤早已就位，只是当年没接上。真删除挪到 `/purge`。

#### `DELETE /api/notes/{note_id}` — 移入纸篓（软删除）

- 只改 `status='archived'` + `archived_at=now()`，**不动 chunks/embeddings/blob**；
- 所有查询本来就过滤 `status='active'` → 移入后立刻从书架与**检索**里消失；
- 因为数据完好，放回是零成本的；
- `204` = 移入成功；`404` = 没这条笔记 / 别人的 / **已在纸篓**（统一 404，不泄漏状态）；
- 保留期 **30 天**，从 **`archived_at`（移入那一刻）**起算，到期由 arq 每日 03:10 彻底删除。

#### `GET /api/notes/trash` — 纸篓清单

```json
{
  "total": 2,
  "retention_days": 30,
  "items": [
    {
      "id": 9354,
      "title": "零代码看懂 CUDA",
      "quality": "core",
      "source_id": 8837,
      "chunk_count": 31,
      "archived_at": "2026-11-25T15:03:06.440334+00:00",
      "expires_at": "2026-12-25T15:03:06.440334+00:00",
      "days_left": 30
    }
  ]
}
```

- 按 `archived_at DESC` 排序（最近丢的在上）；`limit` 1–100（默认 20）、`offset` ≥ 0；
- `days_left` **由后端向上取整算好**（刚移入是 30 而不是 29），前端直接显示，不自己碰时区；
- `retention_days` 随响应下发，前端不写死 30。

#### `POST /api/notes/{note_id}/restore` — 放回书架

- `status='active'` + `archived_at=NULL`，返回 `NoteListItem`（与列表项同形）；
- `200` = 放回成功；`404` = 纸篓里没有这篇（含别人的）。

#### `DELETE /api/notes/{note_id}/purge` — 彻底删除

- **不可恢复**：删 note 行，chunks/embeddings 由 DB 外键级联清空；
- **source 连带**：该笔记的 source 行在没有其他笔记引用时一并删除（`count_notes_for` **含 archived**——
  纸篓里还有别的笔记引用就不能删 source），原文件 blob 提交后从磁盘清（best-effort，失败只记日志）；
- **只认 `status='archived'` 的行**：防止用这个端点绕过软删直接毁一篇 active 笔记 → active 调它得 `404`；
- `204` = 删成；`404` = 纸篓里没有这篇。
- 问答消息里引用过这篇笔记的角标此后点开是 404，引用文字本身不受影响。

#### 30 天自动清理（arq cron）

`WorkerSettings.cron_jobs` 每天 `03:10` 跑 `purge_expired_trash`：跨 owner 扫
`status='archived' AND archived_at < now() - 30 days`，删除行 + 清 orphan source/blob。
⚠️ **cron 只在 worker 存活时触发**——worker 没起 = 自动清理不生效（不报错，只是不删）。

#### 重导去重的连带调整（`_reset_stale_done_job`）

软删后 source 行不再随之删除，于是「删了再重导同一文件」会命中去重（旧 done job），
用户看到「之前导入过，直接复用」但书架空空。判据改为三分类：

| 该 source 下 | 重导行为 |
|---|---|
| 有 `active` 笔记 | 真去重（复用） |
| **无 active 但有 `archived`** | **旧 done 是 stale → 重置 queued 重新入队** |
| 一条笔记都没有 | 复用旧结论（G0/G1 曾拒收 / 空产出） |

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
{ "session_id": 9, "question": "它有哪几种状态", "top_k": 5, "note_ids": [156], "attachments": [{ "name": "config.py", "text": "DEBUG = True" }], "images": [{ "name": "截图.png", "mime": "image/jpeg", "data_url": "data:image/jpeg;base64,…" }] }
```

- `session_id` 缺省/null = 自动建会话（title = 首问前 30 字）
- `top_k` 默认 5，上限 20
- `note_ids`（可选）：用户 @ 某篇笔记 = 告诉 AI「在问什么」；注入 `<referenced_notes>` 块，检索照跑找关联笔记
- **`attachments`（可选，v17，v28 改链路）**：对话附件，用户临时上传给 AI 看的参考文件文本，**不入库**；前端把文档类文件统一交 **`POST /api/parse`** 解析后拿到 `{name, text}` 透传，注入 prompt 的 `<reference_files>` 块（≤5 个，`text` ≤ 40000 字符）。HTML/DOCX/PDF/PPTX 都可直接当附件（解析在 G0 一套口径），**大文件（正文 > 40000 字符）由后端 map-reduce 分析成报告**（`analyzed` 标记，细节可能省略）
- **`images`（可选，v17）**：对话图片附件，用户临时上传给 AI 看的图片，**不入库**，走视觉通道；前端把图片用 canvas 缩到长边 ≤1024px、转 JPEG data URL（`{name, mime, data_url}`）透传，后端复用 `attach_images`（OpenAI 视觉 content 数组）附到最后一条 user 消息（≤4 张）。与命中笔记图（`hit_images`）合并附图，但不受其 `MAX_HIT_IMAGES=2` 上限约束。**需视觉模型**（如 deepseek-flash）；配了纯文本模型（如 glm-5.2）时拒图会自动去图降级重试（答案前置说明），不 500
- 追问先经 LLM 改写检索词；`search_query` 透出实际使用的检索词
- 原子落库 user/assistant 两条消息；user 消息另存 `llm_content`（含检索/引用/附件上下文，多轮前缀对齐用；图片不进 `llm_content`，重放不依赖图）

```json
{
  "session_id": 9, "session_title": "闭包是什么",
  "user_message": { "id": 1, "role": "user", "content": "…", "citations": [], "model": null, "cost_tokens": 0, "cache_hit_rate": null, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "agent_trace": null },
  "assistant_message": { "id": 2, "role": "assistant", "content": "…[1]", "citations": [{ "n": 1, "note_id": 156, "note_title": "…", "heading_path": "…", "snippet": "…" }], "model": "glm-5.2", "cost_tokens": 2169, "cache_hit_rate": 0.4843, "cache_hit_tokens": 15231, "cache_miss_tokens": 16200, "agent_trace": null },
  "search_query": "Promise 有哪几种状态", "took_ms": 7115
}
```

- **`agent_trace`（v31，ADR 0010）**：问答 agent 的每步留痕（授权审计），条目 `{step, kind, tool, arg, result, latency_ms}`（kind ∈ llm / tool / repair / budget；tool 轮的 arg 是检索/搜索词摘要）。**null = 该回答走现有决策树 / user 消息**；开关开且回答出自 agent loop 才有值，随 `GET /api/qa/sessions/{id}` 与流式 `done` 事件一并透出（前端旧版忽略该字段不受影响）

### `POST /api/qa/chat/stream`（SSE 流式，同请求体）

响应 `text/event-stream`，事件序：`meta` → `delta`* → `done`；LLM 失败 → `error`（用户消息已落库）。**v45 起 `error` 的 `detail` 必带真实原因**：LLM 网络层异常（开流连接失败 / 读流中断 IncompleteRead / 读超时「X 秒无新增数据」）在 client 层统一包装成 LLMError 子类上抛，此前裸抛 OSError 只能落进端点通用兜底「流式回答中断,请重试」；agent 模式已放行正文增量后失败**不回落决策树**（会重播一份新答案=重复正文），直接上抛——零增量时回落语义不变。

- `meta`：`{session_id, session_title, search_query, agent, images_attached, model, prepare_ms, user_message}`（`agent` = 本轮是否按 agent 模式跑（默认开）。**v44 起 meta 不再带 `used_web`**——agent 模式下该时点真值结构性未知，只能瞎报；是否联网看 `done` 的 `assistant_message.citations`（任何带 `url` 的出处即联网轮），与非流式 `ChatResponse.used_web` 同源于终值）
- `status`：`{text}`（**v34**，agent 工具轮进度——「正在换词检索笔记库:…」/「正在联网搜索:…」，带检索词；旧前端忽略该事件）
- `delta`：`{text}`（正文增量；推理模型的思考过程不下发；**agent 最终答案逐段放行（v34）**——多帧增量流式，与库内路径同款；联网兜底（决策树路径）仍为整帧）
- `done`：`{assistant_message, took_ms}`（完整消息含出处、cache_hit_rate 与 agent_trace，已落库）

### `POST /api/parse`（v28 新增，📎 附件统一解析，不入库）

`multipart/form-data`，字段 `file`（单个文件，≤ 10 MB）。前端 📎 的文档类附件统一走这里：
后端复用 G0 `parse_file`（与导入同款解析栈），不建 source/job、不入库。

```json
{
  "status": "ok",
  "text": "张三 · 前端工程师 …（全文，或大文件的分析报告）",
  "mode": "full",
  "images": [{ "name": "头像", "mime": "image/png", "data_url": "data:image/png;base64,…" }],
  "warnings": ["大文件已由 AI 分析提炼,细节可能省略;需要原文请拆分后分次上传"],
  "message": null,
  "truncated": false
}
```

- **`status`**：`ok` / `no_text_expected` / `unsupported_format` / `error`（同 G0 语义）；≠ ok 时 `text` 为空，`message` 带用户可读说明（如 .doc 的转换提示）
- **分层**：正文 ≤ 40000 字符 → `mode="full"` 全文直塞；超出且配了 API Key → `mode="analyzed"`（map-reduce：分块并行提取 → 合并 ≤ 20000 字符报告）；没配 key → 退化为截断全文（`truncated=true`）+ warning。正文约超 39 万字符（20 块上限）→ **422** 提示拆分
- **`images`**：文内图片（md/html 的 data URI 与本地图）字节回传，≤ 4 张、单张 ≤ 5 MB（与 chat 的 `images` 上限对齐），前端接进视觉通道缩图；html 外链图不联网抓（与导入同语义）
- 415 = 扩展名不认识（同 `ALLOWED_EXTS`）；413 = 超 10 MB；**没配 API Key 不挡**（文本格式照常解析，只有视觉/扫描件/分析需要 key）
- ⚠️ 扫描件 PDF 逐页视觉识别 / 大文件分析可能耗时几十秒，前端要有进行中提示

### 会话管理

| 端点 | 说明 |
|---|---|
| `GET /api/qa/sessions?limit=50` | 会话列表（**v33 起按最近活跃在前**，`updated_at desc`），`{total, items:[{id,title,created_at}]}` |
| `GET /api/qa/sessions/{id}` | 重开会话，`{id,title,created_at,messages:[Message]}` |
| `PATCH /api/qa/sessions/{id}` | **v36** 重命名会话，body `{"title"}`（1–30 字，剥首尾空白），返回 `{id,title,created_at}`；**不刷 `updated_at`**（重命名不算「活跃」） |
| `DELETE /api/qa/sessions/{id}` | 删会话（消息 CASCADE），204 |
| `GET /api/qa/sessions/{id}/export` | 导出 Markdown（浏览器下载；同时在 `tmp/qa_exports/` 落一份） |

**最近活跃与保留策略（v33）**：会话列表按**最近活跃**排序——落一条消息（user/assistant）即刷新该会话的活跃时间，旧会话发一轮新对话就上浮到顶部；仅打开/查看/导出/重命名**不**刷新。每用户只保留**最新 50 个**会话，更旧的**硬删**（消息/摘要 FK CASCADE 连带删；`qa_sessions.updated_at` 迁移 0018，存量回填 = `created_at`）。裁剪只在**新建会话时**触发（只有新会话出现才可能超上限），删了几个记进后端日志，不建表。已知边界：闲置旧会话可能正开在别的标签页里被裁掉 → 之后该标签页发消息得 `404 会话不存在`，前端兜底「已开新对话」提示。

**`cache_hit_rate` 口径**：prompt 前缀缓存命中占比 = hit/(hit+miss)；上游未命中时不下发字段 → `null`（前端显示「—」）。字段双格式归一：DeepSeek `prompt_cache_hit_tokens` / OpenAI `prompt_tokens_details.cached_tokens`。v29 起消息附带原始计数 `cache_hit_tokens` / `cache_miss_tokens`（双 0 = 该条无缓存数据），前端问答页用它算会话级**平均缓存命中率** Σhit/Σ(hit+miss)（token 加权，比各轮 rate 算术平均更贴近真实缓存效率），替代此前「取最近一条非空 rate」的口径。

**联网兜底**：库内未命中且设置页配了 Tavily key → 自动搜网，回答基于搜索结果、citations 带 `url`（`note_id=null`，前端渲染外链）、`used_web=true`；未配 key 或搜索失败 → 维持「没找到」口径。**挂了图片附件（`images` 非空）时不走联网**——图本身是内容（问的常是「这图讲什么」），搜「图片/内容」只会搜出 OCR 工具之类；让视觉模型直接看图回答（`answer` 路径附图），`[NO_KB]` 兜底也跳过联网、不加「笔记库没有」前缀。

**问答 agent（v31，ADR 0010；**v32 起默认开**）**：问答默认走 agent loop——LLM 在 loop 里决定「换个词再搜（`search_kb`）/ 何时转联网（`search_web`）/ 直接答」，首轮检索与消息前缀字节与旧决策树一致。护栏全在代码（预算 MAX_STEPS/每步超时/总预算，超了回落下述决策树，**最坏情况 = 现状**；引用真实性校验拒收假 `[n]`；挂图不给联网工具）。配额上每答多 1–2 次 LLM 调用（实测负例 8 条均为 2 次），每步留痕进 `agent_trace`。`agent_enabled=false` = 显式关回**旧决策树**（上述检索→相关性判定→[NO_KB]→联网/自答的硬编码树，保留作紧急开关/A-B 对照）；请求/响应契约不变（前端零改动；`status` 事件为新增，旧前端忽略）。**流式路径（v34）**：最终答案边生成边放行（多帧 delta，不再「思考中」后整段跳出），工具轮发 `status` 进度事件；流式模式不做假引用修复轮（已放行文本无法收回）——幻觉编号只从 citations 里剔除，正文死徽章与决策树流式同语义；非流式端点（`POST /api/qa/chat`）行为不变（含修复轮）。

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

**引用保留（v18）**：各条回答的 `citations` 会随对话一起进整理流程。由于每条回答的 `[1]`/`[W1]` 都是本地编号（各自从 1 开始），生成前按**首次出现顺序**重编为全文唯一编号（联网 `W` 前缀、库内不带前缀，同 URL 去重），并在正文末尾追加 `## 出处` 清单，只列正文真正引用到的编号：

```
## 出处

- [W1] 工作流触发器 — <https://respond.io/zh/help/workflows/workflow-triggers>
- [1] Agent 应用开发学习指南 · 方法 — /notes/161
```

**截断兜底（v18）**：`max_tokens=8000`；若上游返回 `finish_reason="length"`，自动续写（最多 2 轮，已生成部分作为 assistant 前缀回传）。续写仍超限时正文末尾追加 `> ⚠️ 本篇因模型输出长度上限被截断…`，不再静默产出半篇笔记。

---

## 6. 设置（**已实现 · 线 Q**）

模型与网关配置存 `app_settings` 单行（字段 NULL 回退 .env 对应项），LLM 调用前现读 → **保存后热生效**（无需重启）。api_key 只写不读。

### `GET /api/settings`

```json
{ "llm_base_url": "http://…", "llm_model": "glm-5.2", "api_key_set": true, "api_key_tail": "abcd", "llm_context_tokens": null, "llm_max_output_tokens": null, "allow_diagrams": true, "agent_enabled": false }
```

### `PUT /api/settings`

```json
{ "llm_base_url": "…", "llm_model": "…", "llm_api_key": "…", "agent_enabled": true }
```

- 三字段均可省略（= 保持不变）；`llm_api_key` 传空串 = 保持不变，传新值 = 覆盖
- **`agent_enabled`（v31，ADR 0010；**v32 起默认开**）**：问答 agent 开关——NULL/true = agent loop（**默认**，迁移 0017 翻转），false = 显式关回旧决策树（紧急开关/A-B 对照，见 §5 问答 agent 段）；agent 失败时回落决策树是代码层兜底，与该开关无关；旧客户端省略该字段 = 保持不变
- 已知边界：worker 的 LLM 配置暂仍读 .env（设置页只影响问答/改写链路）；charter 已改为 DB 优先（见 §8）

---

## 7. 明确不做（前端别要，后端别加）

| 不做 | 原因 |
|---|---|
| 邮箱验证 / 找回密码 / 第三方登录 | 只做用户名+密码；毕设不需要更复杂的流程 |
| 删除账号 | 演示不需要；误删事故风险 > 收益（笔记/会话的删除均已实现） |
| 分页以外的复杂搜索（排序、过滤组合） | 20 个 query 的评测还没出来，先别过度设计 |

---

## 8. 画像 Persona（**已实现**；**v41 由 Charter 扩成「我的画像」**）

**每个账号各自一份章程**（`app_settings` 每用户一行）。首次登录弹问卷向导 → LLM 生成章程 → 存该用户行 → 导入整理（organizer）热生效。
DB charter 优先于内置默认（`resolve_charter`：DB → DEFAULT_CHARTER；项目根的 charter 文件或 `CHARTER` 环境变量仍可覆盖，但已非常规配置）。charter.md 已退役，未填问卷时用 `config.py` 的通用 `DEFAULT_CHARTER` 兜底；好笔记的内容层骨架见 `docs/note-format.md` §5。

**v41 页面语义扩展**（主人 2026-11-26）：「知识库章程」→「**我的画像**」，`/charter` → `/persona`（旧路径 301 重定向）。
页面左右两栏：**左 = 我是谁**（问卷事实 + 据此推导的章程，全部原地可编辑），**右 = 我在做什么**（系统从真实数据数出来的统计）。
`charter` 这个词在内部沿用（导入流水线 G2 判据、worker 都叫 charter），只改对外命名。

**v40 问卷事实化**（主人 2026-11-26 拍板）：旧问卷全是偏好题（「想收什么」「什么算好笔记」），用户面对空库答不出来；新问卷**只问事实**（昵称/年龄段/性别/学历/当前状态/为什么用 baleen），偏好由 LLM 从事实**推导**。「拿不准口径」不再问（固定「宁收低质量」写进生成 prompt）。**昵称**是唯一被问答消费的事实：迁移 0020 `app_settings.nickname`，问答 system prompt 注入（AI 用它称呼用户，没填不称呼）。**v41 起其余五项也落库**（迁移 0021），但只供画像页左栏展示，**不进 prompt**。

### `GET /api/persona`

```json
{
  "nickname": "小明", "age_band": "18-24", "gender": "男",
  "education": "本科在读", "status": "在读学生", "why": "课程学习",
  "has_charter": true, "charter": "## 这是谁的库\n……"
}
```

`has_charter=false` = 当前账号还没有章程（前端进问卷流程）。五项事实为 `null` = 这题没答过（老用户迁移 0021 之前的数据本来就不存在，**不做假回填**）。

### `POST /api/persona/generate`

问卷答案（六个事实，**全部可空**）→ LLM 从事实推导章程文本（**不落库**，前端预览编辑后再 PUT）。

```json
{
  "nickname": "小明",
  "age_band": "18-24",
  "gender": "男",
  "education": "本科在读",
  "status": "在读学生",
  "why": "课程学习、课设沉淀"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `nickname` | string | 怎么称呼你（≤30 字；问答时 AI 用它称呼用户） |
| `age_band` | string | 年龄段：<18 / 18-24 / 25-30 / 31-40 / 40+ |
| `gender` | string | 性别：男 / 女 / 不说 |
| `education` | string | 学历：高中及以下 / 本科在读 / 本科 / 硕士及以上 |
| `status` | string | 当前状态：在读学生 / 在职 / 自由职业 / 待业 / 退休 |
| `why` | string | 为什么用 baleen（一句话） |

```json
{ "charter": "## 这是谁的库\n「小明」的库，一名在读本科生……" }
```

### `PUT /api/persona`

保存左栏（章程 + 可选的昵称与五项事实）。**热生效**：导入整理下次调用即用新章程，问答下次请求即用新昵称。

```json
{
  "charter": "## 这是谁的库\n……",
  "nickname": "小明",
  "age_band": "18-24",
  "gender": "男",
  "education": "本科在读",
  "status": "在读学生",
  "why": "课程学习"
}
```

- `charter` **必传**（≤20000 字）；空串 = 清空（回退内置默认章程）
- 其余六个字段**全部可选**：缺省 = 不动，空串 = 清空；首尾空白自动剥除
- 返回同 `GET` 的完整对象（未传字段保持原值 —— 只改昵称时不必回传整份事实）
- **昵称消费链**：问答（agent 与决策树两条路径）的 system prompt 注入「用户的名字是『X』」（`resolve_nickname` 每请求现读，保存后热生效；没填不注入，prompt 与旧版一致）

### `GET /api/persona/stats`

右栏全部统计。**纯只读、每次现算**（主人拍板暂不把画像注入问答 prompt —— 不注入就不存在摘要陈旧问题）。

```json
{
  "size": { "notes": 62, "chunks": 2580, "sessions": 46, "asks": 297 },
  "sources": { "video": 37, "doc": 25 },
  "quality": { "core": 49, "normal": 11, "reference": 2 },
  "answers": { "kb": 135, "web": 63, "self": 91, "total": 289 },
  "top_cited": [{ "id": 156, "title": "闭包 —— 面试必问，装逼必备", "hits": 58 }],
  "dead_notes": { "total": 26, "items": [{ "id": 70, "title": "Git Bash 常用命令备忘", "quality": "core" }] },
  "self_answered": { "total": 86, "items": ["为什么 Docker 容器里的进程比虚拟机更重？"] },
  "activity": [{ "week": "2026-11-23", "notes": 8 }],
  "topics": [{ "topic": "编程开发", "notes": 22 }],
  "untagged": 0
}
```

| 字段 | 口径 |
|---|---|
| `sources` | 视频转写 vs 文档导入。判据 `sources.parse_meta ? 'video_url'`。视频笔记落地后就是普通 md，**画像里与文档笔记完全平等** |
| `answers` | **互斥三分，三项相加 = `total`**：`kb` = 有笔记引用（不论是否也联网）；`web` = 无笔记引用但有网页引用；`self` = 两者都没有（AI 用自身知识答） |
| `top_cited` | 被引用次数最多的笔记 Top 5（只数 note 引用，网页引用是外部内容不算「我的笔记」） |
| `dead_notes` | 一次都没被引用过的活跃笔记：前 30 条 + **真实总数** |
| `self_answered` | 库和网都没用上的问题（前 30 条 + **真实总数**，已过滤问候语）。**这才是该补笔记的地方** |
| `activity` | 近 8 周每周新增笔记数，空周补 0（只回有数据的周会让「停了两周」看不出来） |
| `topics` | 主题分布，读 `notes.tags`；空 = 还没分析过 |
| `untagged` | 还没打主题标签的活跃笔记数 |

⚠️ **口径警告**：`kb` 与 `web` 早期版本各自计数（有交集），会让前端算出 47%+37%+31%=115% 的荒唐百分比。现在是互斥口径，`web` 已排除「也有笔记引用」那部分。**不许退回旧口径。**

### `POST /api/persona/topics`

给未打标的笔记批量打主题标签，写回 `notes.tags`（该列 0002 建表至今无写入方，本端点是第一个）。

| 查询参数 | 说明 |
|---|---|
| `retag` | `true` = 清空全部已有标签重打（标签集合漂移成近义词后收敛用） |

```json
{ "tagged": 62, "remaining": 0, "topics": [{ "topic": "编程开发", "notes": 22 }] }
```

- **增量**（默认）：只处理 `tags` 为空的笔记，打过的跳过。全打完时返回 `tagged=0, remaining=0`，不重复烧 token
- **两阶段打标**：① 全部标题 → 归纳 ≤8 个主题名；② 拿固定主题表分批打标（单阶段实测会让标签漂移成「AI」「AI与大模型」「AI与智能体」三个近义词并肩）
- LLM 报错或输出被截断 → **502**，`detail` 是中文可读原因

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-28 | v45 | **流式 `error.detail` 必带真实原因**（2026-11-28 自制网关 429 诊断定案）：LLM 网络层异常（开流连接失败 / 读流中断 IncompleteRead / 读超时「X 秒无新增数据」）在 client.chat_stream 统一包装成 LLMError 子类带原因上抛——此前裸抛 OSError，agent 的 except LLMError 接不住，SSE 端点只能报通用兜底「流式回答中断,请重试」。agent 流式已放行正文增量后失败改**重抛不回落**（回落=决策树重播一份新答案接在已放行文本后=重复正文事故）；零增量回落语义不变。+7 测（client 5 + agent 2），双库 743 同口径 |
| 2026-11-27 | v44 | **SSE `meta` 事件移除 `used_web` 字段**（挂账②收口：C1 批时点语义刻意未统一——agent 模式下 meta 时点真值结构性未知、恒 False，决策树模式下是预检时点值，同一字段两种语义且前端零消费）。移除后唯一真源 = 终值：非流式 `ChatResponse.used_web` 照旧，流式看 `done` 的 `assistant_message.citations`（带 url 即联网轮），口径天然一致。等价性测试同步改写（原「契约差异各自钉住」注释随之作废）+ 补流式侧「agent 回落+联网」API 级测试（原批欠账） |
| 2026-11-26 | v43 | **抖音视频封面（海报卡）**：`GET /api/sources/{id}` 响应加 `video_cover_url`（可直接当 `<img src>` 的相对 URL `/api/media/{key}`；null = 无封面/非抖音/未提取）。抖音不支持网页内嵌播放，前端原先把抖音塞进 B 站 iframe 的 16:9 **黑底容器**，变成一个 850×480 的黑洞里飘两行小字（主人反馈「这是一个黑色的，不好看」）。现在前端两种形态：有封面 → **海报卡**（9:16 竖版封面 + 标题 + 外链；抖音是竖屏平台，实测封面 1080×1920，按 16:9 裁会切掉标题和人物）；无封面/粘贴瞬间 → **一行高提示条**（69px，纸面底色 + 站点名 + `在抖音中打开`）。**封面存 media 内容寻址 key 而不是抖音原 URL**：抖音封面是签名链（实测 `x-expires` 两周过期），存原样会变裂图。**封面在下载视频之前就写库**（新增 `cover_cb` 早回调），否则 40 分钟视频要让海报卡白等十几分钟（实测转写阶段 `video_cover_url` 仍是 null 的坑）。B 站播放器**不动**（iframe 的 16:9 黑框是正确形态） |
| 2026-11-26 | v42 | **画像页收成一屏**（主人反馈「要拉滚动条才能看到所有内容，用多了数据更多不是一直拉很长的条」）：右栏来源/质量/回答方式**三块并排**、块间距 26→22px、两个清单（死库存/纯自答）改成**默认折叠**的一行标题+数量，点开才展开且列表自带 260px 上限内滚。前端整页改为**两栏等高各自内滚、整页零滚动**（复用 `stage--fill` 高度链，1440/1280/1100 三档实测滚 0px）。清单上限 8→**30 条**（折叠态不占高度，展开时用户真想看）。**`self_answered` 响应形状改 `{total, items}`**（与 `dead_notes` 一致）——原先是裸数组，页面把 len 当总数显示「30」，而真实自答 86 条，数字会骗人；现在 total 走独立 COUNT 查询。§8 同步 |
| 2026-11-26 | v41 | **「知识库章程」扩成「我的画像」**（主人拍板：左右布局，左边是用户开始用时写的问卷内容，右边是 AI 自统计的偏向统计图；页面/路由不能叫章程）：路由 `/charter`→`/persona`（旧路径重定向），顶栏「知识库章程」→「我的画像」，端点前缀 `/api/charter`→`/api/persona`（内部 `charter` 概念不动，只改对外命名；端点 34→**36**）。**迁移 0021** 补存五项问卷事实（`age_band`/`gender`/`education`/`status`/`why`）——v40 只存了昵称，其余五项喂完 LLM 就丢，左栏没数据源；全部 nullable，老用户留 NULL 不假回填。新增 `GET /api/persona/stats`（八块统计，纯只读现算：规模/来源/质量/**回答方式互斥三分**/Top5 引用/死库存/纯自答问题/周活跃）；新增 `POST /api/persona/topics`（两阶段 LLM 主题打标写回 `notes.tags` 增量 + `?retag=true` 重打）。**口径修正**：`answers` 的 kb/web 早期各自计数会相加超 100%，改互斥口径（`kb` 有笔记引用、`web` 无笔记引用但有网页引用、`self` 两者皆无），三项相加 = total。§8 重写 |
| 2026-11-26 | v40 | **问卷事实化 + 昵称问答称呼**（主人拍板「问卷填的内容」是主要问题）：旧问卷全是偏好题（「想收什么」「什么算好笔记」），用户面对空库答不出来；新问卷**只问六个事实**——昵称/年龄段/性别/学历/当前状态/为什么用 baleen（全部可空），偏好（收什么/怎么整理/口吻深度）由 LLM 从事实**推导**；「拿不准口径」不再问，固定「宁收低质量」写进生成 prompt（本来就是代码默认值）。**昵称结构化消费**：迁移 0020 `app_settings.nickname`，`GET /api/charter` 响应加 `nickname`，`PUT` 可选连带保存；问答（agent+决策树两路径）system prompt 注入称呼行（`resolve_nickname` 现读热生效，没填不注入）。§8 重写；测试改写 3+新增 1（六事实进 prompt/固定规则入 prompt/旧字段忽略/昵称往返）+ 问答昵称注入断言 1 |
| 2026-11-26 | v39 | **个人中心整页下线 + `GET /api/profile` 删除**（主人拍板「先把个人中心裁了」——铁律推演余波：v38 后该页只剩头像条+章程按钮，已无独有信息）：前端删 `/profile` 路由与 ProfileView 视图文件、书立「我的」按钮；工作台顶栏「个人中心」链接改「**知识库章程**」（/charter 在工作台态的唯一入口原本在个人中心里，入口平移顶栏，与引导态顶栏同款）；账号身份顶栏常驻（用户名），注册时间无展示位不再提供；`/profile` 旧 URL 走 catch-all 回 `/qa`。**后端同步删端点**（唯一消费方就是该页——无消费方的端点即死代码）：profile 端点实现文件+`main.py` 路由挂载+8 个 profile 测试全删；§10 整节移除。账号信息仍可走 `GET /api/auth/me`（§9） |
| 2026-11-26 | v38 | **`GET /api/profile` 收缩到两项**（主人 2026-11-25 铁律「不重叠功能和内容」）：删 `settings`（与 `GET /api/settings` 六字段完全同口径——原测试直接断言两者相等，自证重复）、`stats`（前端使用统计区块已删，无人消费）、`charter.preview`（截断不可读，真源在 `GET /api/charter`）。只留 `user`（注册时间无处可查，本端点独有）与 `charter.has_charter`（前端据此决定按钮文案）。响应从四块变两块，密钥相关内容完全不出此端点。无迁移、无新端点；前端 profile 类型定义同步收缩 |
| 2026-11-25 | v37 | **笔记纸篓（软删除）**：§3 `DELETE /api/notes/{id}` 从真删除改为**移入纸篓**（`status='archived'` + `archived_at`），依据 ADR 0003 §3⑤ 原设计（软删+检索过滤+定时清理，字段与过滤早已就位但从未接上）。新增 `GET /api/notes/trash`（清单带 `archived_at`/`expires_at`/`days_left`，`retention_days=30` 随响应下发，`days_left` 后端向上取整）、`POST /api/notes/{id}/restore`（放回，零成本）、`DELETE /api/notes/{id}/purge`（彻底删除，只认 archived 防绕过）。保留期 **30 天**，从 `archived_at`（移入那一刻）起算，arq cron 每日 03:10 清理（`purge_expired_trash`；⚠️ 依赖 worker 存活）。迁移 **0019** `notes.archived_at` + 局部索引（`WHERE status='archived'`）。连带：`_reset_stale_done_job` 改三分类判据（无 active 但有 archived → 重导必须重跑，否则「删了再导入」被静默去重）。前端退役 localStorage 纸篓（旧方案换浏览器即空、不同账号共用同一 key），改查后端；「篓」引用标随之下线（检索已不命中 archived）。**背景（主人 2026-11-25 三条诉求）**：纸篓应 30 天自动删除、不同用户不互通、换浏览器纸篓还在 |
| 2026-11-25 | v36 | **会话重命名**：§5 会话管理新增 `PATCH /api/qa/sessions/{id}`——body `{"title"}`（1–30 字，剥首尾空白；长度与首问标题同口径 `TITLE_CHARS=30`），返回 `{id,title,created_at}`；不存在/非本人会话 404，空白 422。**不刷 `updated_at`**——「最近活跃」只认落消息，重命名不算活跃，列表位次不动。**CORS `allow_methods` 补 `PATCH`**（B5 显式清单的首个 PATCH 路由，分域部署时预检会拒，开发态 vite 同源看不出；`test_cors.py` 同步——清单外拒绝例改 TRACE）。配套前端（UI 设计阶段）：侧栏会话行右键菜单（删除/导出/重命名）+ 行内编辑重命名 |
| 2026-11-22 | v35 | **转写治本三件套**（spike 实测拍板，eval/runs/transcribe_spike_2026-11-22.md）：①whisper 模型 small→**large-v3-turbo**（专名原生准确率接近 large-v3、速度等同 small、显存 Δ2.3GB）+**language=None 自动检测**（zh 强制对英文视频是灾难放大器——8093 专名错字 ≥20 的根源）；模型文件预置在仓库根之外的 models/ 目录（.env `WHISPER_MODEL_DIR` 指路，生产禁运行时在线下载——spike 在线 load 63min 教训；未配置退回 hub 名）。②worker 管线转写后、organizer 前插 **LLM 自校正 pass**：盲修疑似错听的专名/术语（零词典宁缺毋滥，只出修改清单程序化应用），修正留痕写 `parse_meta.llm_transcript_fix`；LLM 失败降级用原转写（凡失败必留痕），输入 3 万字符封顶，max_tokens 16000（推理网关 8000 全烧 reasoning 教训）；client 可注入可 mock。③`video_log` 新 stage「转写校正中…」；pipeline video_keep 白名单放行新键。测试 +7（turbo/None 参数断言、校正应用/降级/skipped/封顶、留痕 merge）；真链路验证留给下一批喂料 |
| 2026-11-20 | v34 | **agent 流式输出**（主人验收反馈：「我希望前端流式输出AI内容，而不是从『思考中』之后直接跳出整段内容」）：①`chat_stream` 加 `tools`/`tool_choice`/`timeout` 透传 + `tool_calls` 流式分片拼装（流末事件，与非流式 `ChatResult.tool_calls` 同形）。②`agent.py` 重构为 `run_agent_iter` 异步生成器：`streaming=True` 产 `status`/`delta` 事件、[NO_KB] 首行门扣下标记答案、流式不做假引用修复轮（已放行文本无法收回——幻觉编号只从 citations 剔除，死徽章与决策树流式同语义）；`run_agent` 为薄包装，非流式行为不变（含修复轮）。③流式端点 agent loop 移入 `gen()`：事件序 `meta → status* → delta* → done`，最终答案**逐段放行**（多帧 delta），工具轮发 status（带检索词）；agent 回落时决策树前置（4.5 预检+附图）gen 内现算，最坏=现状；`meta.agent` 语义改为「本轮是否按 agent 模式跑」。④前端 `qa.ts` 加 `onStatus`；问答页思考行显示工具轮进度。测试 +7（client 3 + iter 4）；真链路实测：直答题 386 帧 delta、换词/转网题 3 条 status + 594 帧 delta |
| 2026-11-20 | v33 | **会话「最近活跃」排序 + 每用户保留最新 50 个**（主人 2026-11-20 拍板）：§5 会话列表从「按创建时间」改为「按最近活跃」（`updated_at desc`）——落一条消息（user/assistant）即刷该会话活跃时间，旧会话发一轮新对话上浮到顶；仅打开/查看/导出不刷新。保留策略：每用户只留最新 50 个会话，更旧的**硬删**（消息/摘要 FK CASCADE 连带删），只在新建会话时触发裁剪（删几个记日志不建表）。迁移 0018：`qa_sessions.updated_at`（NOT NULL，server_default now()）+ `(owner_id, updated_at DESC)` 索引 + 存量回填 `updated_at = created_at`；老用户零感知（只加列回填，digest/缓存不受影响）。响应字段不变（`created_at` 照旧返回，前端不显示日期）。已知边界：闲置旧会话开在别的标签页时可能被裁 → 之后发消息 404，前端兜底「已开新对话」 |
| 2026-11-20 | v32 | **问答 agent 默认开**（主人验收拍板：「还要我手动去打开吗，我们都已经是 agent 模式了，旧对话模式不要再是默认」）：`agent_enabled` 语义翻转——NULL/true = agent loop（**默认**），false = 显式关回旧决策树（紧急开关/A-B 对照）。迁移 0017：`app_settings.agent_enabled` server_default false→true + 存量行全部置 true；agent 失败回落决策树的代码层兜底不变（ADR 0010「最坏情况=现状」不拆）。`negative_eval.py` 加 `--no-agent`（临时关 agent 跑决策树基线，跑完恢复原值；`--agent` 改为显式确保开）。§5/§6 描述同步；测试基线 631（旧决策树测试在 fixture 显式关 agent，agent 默认开有专项测试） |
| 2026-11-20 | v31 | **问答 agent 灰度（ADR 0010）**：§6 设置加 `agent_enabled`（**默认关**；关 = 走现有决策树，行为与现状完全一致；开 = answerer 决策树上翻 tool-call loop——LLM 在 loop 里决定换词再搜 `search_kb` / 何时转联网 `search_web` / 直接答，预算三道闸 MAX_STEPS/每步超时/总预算超了回落决策树，最坏情况 = 现状）。§5 消息响应（`user_message`/`assistant_message`/`GET /sessions/{id}`/流式 `done`）新增 `agent_trace`（每步留痕 `{step, kind, tool, arg, result, latency_ms}`，null = 老路径/user 消息）；流式 `meta` 事件新增 `agent` 标记。迁移 0016：`app_settings.agent_enabled`（默认 false）+ `qa_messages.trace`（JSONB 授权审计）。前端契约不变（零改动，新增字段旧版忽略）。**背景**：`_has_relevant_hit` 字面闸判「沾边」就混合作答（n02 Hadoop 案例，缺陷台账失败模式④）；「这段只讲了起源没回答组件清单，该换词再搜或转联网」是只有 LLM 能做的语义判断。首跑教训（已修）：addendum 必须显式声明对旧规则 2（[NO_KB]+自身知识）的优先级，否则模型从不调 search_web |
| 2026-11-20 | v30 | **B 站扫码登录半自动获取 SESSDATA**：§2 新增 `POST /api/bilibili/login`（弹真浏览器扫码，同步阻塞 ≤3.5 分钟，落库返回 uid）+ `GET /api/bilibili/login`（登录态查询，SESSDATA 永不回显）。被风控的 whisper 导入在 worker 兜底自动触发扫码（`video_stage`/`video_log` 提示 + 扫码成功原地重试兜底）；视频笔记页加「B 站登录」手动按钮。迁移 0015：`app_settings` 加 `bilibili_sessdata` / `bilibili_sessdata_at` / `bilibili_uid` 三列（DB 优先热生效，`.env` 的 `BILIBILI_SESSDATA` 仅回退）。**背景**：B 站浏览器兜底需要 SESSDATA 门票但 F12 手抄从未发生过（11-19 财富自由 5583 两次导入死于此）；扫码全程真浏览器（红线：SESSDATA 不做 HTTP 重放——TLS 指纹打分实测无效），既有消费者不变只换值的来源 |
| 2026-11-18 | v29 | **问答缓存命中率改会话平均**：`§5` 消息响应（`user_message` / `assistant_message` / `GET /api/qa/sessions/{id}` 各消息 / 流式 `done` 事件）新增 `cache_hit_tokens` / `cache_miss_tokens` 原始计数（int，双 0 = 该条无缓存数据，与 `cache_hit_rate: null` 同判）；前端问答页输入框下方的「缓存命中率」（取最近一条非空 rate）改为**「平均缓存命中率」** = Σhit/Σ(hit+miss)，token 加权聚合全会话，联网搜索轮（双 0）天然不进分母。**背景**：按轮次算术平均会把长消息（几十 k 缓存 token、命中率高）和短消息（几百 token、命中率低）等权摊平，偏离真实 token 级缓存效率；`cache_hit_rate` 逐消息字段保留不变 |
| 2026-11-17 | v28 | **📎 附件统一后端解析 + 大文件分层分析**：①新增 `POST /api/parse`（§5，multipart 单文件 ≤ 10 MB，不入库）——复用 G0 `parse_file`，docx/pdf/pptx/md/csv/txt/html 一套口径；响应 `{status, text, mode, images, warnings, message, truncated}`。②新增 `service/parse/html_parser.py`（标准库 HTMLParser 零新依赖）：剥 style/script/head、块级标签断行、h1-h6 产 heading；`<img src="data:...">` 经 `media_sink` 提取字节（导入路径存库/解析路径回传前端视觉通道），外链不联网抓；`PARSERS` 加 `.html/.htm`（`ALLOWED_EXTS` 自动扩大，导入同步支持 HTML）；`media_sink` 特判从 md 扩到 md/html。③新增 `service/qa/file_analyzer.py`（map-reduce）：正文 > 40000 字符分块（20000/块，重叠 500）并行 LLM 提取 → 合并 ≤ 20000 字符报告（`mode=analyzed`）；20 块上限 → `FileTooLargeError` 422 提示拆分；单块失败降级保留原文头部。④`POST /api/qa/chat` 的 `attachments[].text` 上限 20000 → 40000（与解析分层对齐）。⑤前端 `QaView` 📎 文档类全部走 `/api/parse`（删前端白名单/截断/编码逻辑），`api/parse.ts` 新增；chips 加「已分析」标记；解析中显示进行中提示。**背景**：HTML 简历 18 万字符 90% 是 base64，旧前端读文本被 20000 截断；附件全文进 `llm_content` 每轮重放是硬约束，故分层而非放宽上限 |
| 2026-11-16 | v27 | **视频导入白盒进度日志**：`GET /api/sources/{id}` 响应新增 `video_log`——提取+整理全程的阶段性日志数组，条目 `{ts, stage, pct}`（时间戳 epoch 秒/阶段文本/百分比，非视频导入为 `null`）。worker `_update_video_stage`/`_fail_video_job` 每次埋点追加一条（去重守卫：同 stage 连续调用只刷新 pct；截断保留最近 50 条）；pipeline 在 G0 落盘后追加「解析完成」、organizer 前后追加「AI 整理笔记中」/「AI 整理完成」（基于 `SourcesRepo.get_parse_meta` 绕过 ORM 缓存读最新值，顺带修 `pipeline.py` headings_rewritten 覆盖过时 parse_meta 的隐患）；前端 VideoView `.progress-card` 内新增滚动日志区渲染时间戳+阶段+百分比。复用现有 2s 轮询与 `parse_meta` 持久化，刷新页面日志仍在；不上 SSE/WebSocket |
| 2026-11-14 | v26 | **个人中心**:§10 新增 **`GET /api/profile`** 聚合端点——用户信息(id/username/注册时间)+ 使用统计(notes/sources/qa_sessions/qa_messages,owner 隔离,notes 只数 active)+ 章程预览(前 300 字,完整内容仍走 `GET /api/charter`)+ 配置掩码视图(与 `GET /api/settings` 六字段同口径,密钥永不回显全文);纯读聚合,复用现有 repo 与 `resolve_llm_config`,无新表无迁移;前端新增 `/profile` 页(导航加「个人中心」入口),只读展示,编辑动作跳章程/设置页 |
| 2026-11-14 | v25 | **抖音免 cookie 提取 + 导入进度条**：①抖音模式改走 Playwright（`douyin_extractor.py`，channel=msedge 用系统 Edge）打开视频页拦截详情接口，元信息+播放地址全拿到，CDN 直连下载——**用户零配置**，废除 v24 的 `douyin_cookies` 设置字段（`PUT /api/settings` 移除，DB 列保留标注废弃；实测 ttwid 单值/首页 cookie/蜘蛛页全被抖音反爬封死，手动复制完整 Cookie 头对普通用户不可用）；②`GET /api/sources/{id}` 响应新增 `video_progress`（0-100）与 `video_est_seconds`（转写预计秒数），前端进度条在 whisper 阶段按视频时长插值（base 模型 ~1x 实时）；③worker `deduped=True` 语义收紧为仅 job done（失败重试不再被误报「之前导入过」），入队前清 arq 旧结果键；④organizer prompt：质量评级标准具体化（评整理后的笔记本身，视频/字幕来源不构成降级理由）+ 多并列大主题升多章结构指引（内部行为，无接口变化） |
| 2026-11-13 | v24 | **抖音视频支持**：`POST /api/sources/url` whisper 模式支持抖音链接（`v.douyin.com` / `www.douyin.com`）；`video_extractor.py` 用 yt-dlp 统一提取元信息（替代 B 站 view API，平台无关）；新增 `douyin.py`（URL 检测 + ID 提取）；抖音 cookies 通过设置页配置（`PUT /api/settings` 的 `douyin_cookies` 字段，粘贴 cookie 字符串即可，无需手动导出文件）；抖音只支持 whisper 模式（subtitle → 400）；前端 VideoView 检测抖音 URL 显示「在抖音中打开」链接 + 强制 whisper；`build_video_markdown` 标签通用化（"UP主"→"作者"、"B站原链接"→"原链接"） |
| 2026-11-13 | v23 | **whisper 模式异步化**：`POST /api/sources/url` 的 `mode="whisper"` 从 API 同步阻塞改为 worker 后台异步——API 立即返回 201（创建占位 source + job），worker 在后台做 下载音频→whisper 转写→写 markdown→调 `run_import_async(source_id=...)` 入库；`GET /api/sources/{id}` 响应新增 `video_stage` 字段（`"下载音频中…"` / `"whisper 转写中…"` / `"❌ 错误"` / `null`）；whisper 时长上限从 30 分钟提高到 60 分钟（worker `_timeout=7200`）；source.sha256 用占位值 `"video:{bvid}:whisper"`（dedupe 基于 BVID）；subtitle 模式不变（保持同步快速路径） |
| 2026-11-12 | v22 | **视频笔记页 + whisper 模式**：`POST /api/sources/url` 新增 `mode` 参数（`"subtitle"` / `"whisper"`）；whisper 模式下载音频+whisper 转写+帧描述（不依赖 B 站字幕系统，内容可靠）；新增前端「视频笔记」页（B 站嵌入式播放器 + 模式选择 + 导入进度） |
| 2026-11-12 | v21 | **G2 从判官变编辑 + 用户上传必须配 API Key**：G2 归属闸 + 标题整理合并为「AI 笔记整理」（`organizer.py`）——LLM 读 `note-format.md` 契约重写整篇 markdown（补标题/组织结构/去噪），无可整理内容→不入库，LLM 异常→降级原文入库；`POST /api/sources` 和 `POST /api/sources/url` 删除 `no_llm` 参数，没配 API Key → 400（防止未整理内容污染知识库）；charter 从导入流水线中去掉（API/问卷暂留）；`no_llm` 保留为内部参数（chat save-note 用） |
| 2026-11-12 | v20 | **B 站视频字幕导入**：§2 新增 `POST /api/sources/url`（JSON body `{url}`，响应同 `POST /api/sources`）——后端纯 HTTP（urllib，零新依赖）拉取 B 站 CC/AI 字幕拼成 markdown，走现有导入管线；无字幕→400，B 站接口不可达→502；需 `.env` 配 `BILIBILI_SESSDATA` 才能拿到大部分视频字幕；有章节按章节分 heading，字幕片段按句末标点+时间间隔合段 |
| 2026-11-09 | v19 | **charter.md 退役 + 笔记契约内容层**：§8 charter 优先级描述更新——charter.md 文件退役（未填问卷时用 `config.py` 通用 `DEFAULT_CHARTER` 兜底，不预设"软件本科生"身份）；好笔记内容层骨架（用自己的话/原子化/定义+例子/三段可复现/出处）见 `docs/note-format.md` §5，并注入 charter 生成 prompt 的「判据」「笔记长什么样」两节。API 行为不变（GET/POST/PUT 端点与字段不变，仅回退描述更新）|
| 2026-11-05 | v18 | **整理笔记保留出处链接 + 截断兜底**：① `save-note` 的组织流程把各条回答的 `citations` 一起带进去——原先只传 role/content，导致笔记正文里的 `[W1]` 变成无指向的裸徽章。各条回答的 `[1]`/`[W1]` 是**本地编号**（都从 1 开始），直接拼成一篇会串号，故按首次出现顺序重编为全文唯一编号（`[W1..Wn]` 联网、`[1..n]` 库内，同一 URL 去重）。② 笔记末尾自动追加 **`## 出处`** 清单，只列正文真正引用到的编号：联网写 `<https://…>`（markdown autolink，被 chunker 拍平后 URL 仍保留），库内写站内路由 `/notes/{id}`。③ 前端笔记阅读页把 `[Wn]` 徽章升级为可点链接（外链 `target=_blank rel=noopener noreferrer`，库内跳笔记详情；无清单的老笔记降级为静态徽章）；补 `.sec` 容器的徽章样式（原先只挂 `.md`，笔记页的引用标记连徽章样式都没有）；`styles.css` 里误用的 scoped `:deep()`（全局样式表不生效）已改为普通选择器。④ 组织笔记的 `max_tokens` 从 3000 提到 **8000**，并在 `ChatResult` 新增 **`finish_reason`**：撞上 `length` 时按轮续写拼回（最多 2 轮），续写仍超限则在正文末尾追加显式截断警告——原先截断是静默的（一篇 5 表笔记停在第四张表第二行，末尾留一个孤立的 `|`）。**已在编辑笔记时手动补齐表格的情况不受影响** |
| 2026-11-05 | v17 | **导入与对话附件职责分离 + 图片附件**：① §5 `POST /api/qa/chat`（及 `/chat/stream`）请求体新增可选 **`attachments`**：`[{name, text}]` 对话文本附件（≤5，`text` ≤ 20000 字符），注入 prompt 的 `<reference_files>` 块，**不入库**——前端把文本类文件（txt/md/json/csv/代码等）读成文本透传；PDF/DOCX/PPTX 等二进制文档走「笔记库页导入 → `note_ids` 引用」路径。② 新增可选 **`images`**：`[{name, mime, data_url}]` 对话图片附件（≤4），**不入库**，走视觉通道——前端 canvas 缩到长边 ≤1024px + JPEG 透传，后端复用 `attach_images`（OpenAI 视觉 content 数组）附到最后一条 user 消息，与命中笔记图合并但不受其 2 张上限约束；流式 meta `images_attached` 含附件图。**需视觉模型**（deepseek-flash）；配纯文本模型（glm-5.2）时拒图自动去图降级重试（答案前置说明，不 500）。后端 answerer 加 `_reference_files_block`（与 `<referenced_notes>` 平行、按 token 截断）、`answer_web` 加 `images`、短路条件含 attachments+images。③ 前端：对话框删 `/导入` 命令与拖放导入，改为 📎 附件（文本+图片 chips 随提问消耗、不落库）；笔记库页（LibraryView）新增「导入笔记」入口 + 进度面板，导入完成自动 `load()` 刷新书架（解掉「导入后要 F5 才看得见」） |
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


