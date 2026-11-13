# ADR 0009: 视频异步提取（whisper 模式从 API 同步改为 worker 后台）

- **状态**: 已采纳
- **日期**: 2026-11-13
- **关联**: ADR 0007（B 站字幕导入）, ADR 0008（G2 编辑器）

## 背景

v22 的 whisper 模式在 API 请求内同步执行 `extract_video_content`（下载音频 → whisper 转写 → 帧提取），阻塞 HTTP 请求 1-2 分钟。长视频（>30 分钟）直接 502 超时。临时方案加了 30 分钟时长上限，但用户需要导入 1 小时视频（如"AI Infra 一小时入门"）。

## 决策

将 whisper 模式从 **API 同步阻塞** 改为 **worker 后台异步**：

1. **API**（`POST /api/sources/url` whisper 分支）：创建占位 source + job，入队 `extract_and_import_video`，立即返回 201。不阻塞。
2. **Worker**（`extract_and_import_video`）：后台做 下载音频 → whisper 转写 → 写 markdown → 调 `run_import_async(source_id=...)` 入库。
3. **前端**：轮询 `GET /api/sources/{id}` 读 `video_stage` 字段显示多阶段进度（下载音频中 → whisper 转写中 → AI 整理笔记中）。

### 占位 sha 策略

source.sha256 = `"video:{bvid}:whisper"`（占位值，不更新为真实 sha）。
job.dedupe_key = `"import:video:{bvid}:whisper"`（与 source.sha256 匹配，`get_source_status` 查找逻辑不变）。
真实 sha 存入 `parse_meta.content_sha256` 供溯源。dedupe 基于 BVID（同一视频重复导入 → job 已 done → API 返回 deduped）。

### pipeline 改动

`run_import_async` 新增 `source_id: int | None = None` 参数：
- `source_id=None`（默认）：原有路径，从 sha256 创建 source + job。
- `source_id=<int>`（视频异步路径）：使用 API 预创建的 source + job，跳过 dedupe/创建，直接存 blob + 更新 source 内容 + claim job + 跑 G0→G1→organize→chunk→embed。

### 超时

- `import_file`：保持 `job_timeout=900`（15 分钟，文件导入不需要更长）。
- `extract_and_import_video`：通过 `enqueue_job(_timeout=7200)` 单独给 2 小时（whisper base GPU ~1x 实时，60 分钟视频 ≈ 60 分钟转写）。
- `MAX_WHISPER_DURATION`：从 1800（30 分钟）提高到 3600（60 分钟）。

### 进度传达

Worker 通过独立 DB session 更新 `source.parse_meta["video_stage"]`：
- `"排队中"` → API 创建时设置
- `"下载音频中…"` → 提取阶段
- `"whisper 转写中…"` → 转写阶段（实际 whisper 不分阶段，更新为"下载音频中"后直接跑 whisper）
- `"❌ {错误}"` → 提取失败
- pipeline 接管后清除 `video_stage`，前端通过 `parse_status` + `job.status` 显示进度

## 不变的部分

- **subtitle 模式**：保持同步快速路径（秒级拉 B 站字幕）。
- **`import_file`**：原文件导入逻辑完全不变。
- **`gate/g2.py` / `organizer.py`**：不受影响。
- **`bilibili.py`**：字幕拉取逻辑不变（worker 的 subtitle 模式复用）。

## 后果

- ✅ API 不再阻塞，长视频（≤60 分钟）可导入。
- ✅ 前端显示多阶段进度（下载→转写→整理→入库）。
- ✅ worker 崩溃可自动重试（job 保持 queued，arq 重试）。
- ⚠️ source.sha256 是占位值（非真实内容 sha），内容寻址 dedupe 不适用于视频导入（但 BVID dedupe 覆盖了常见场景）。
- ⚠️ `max_jobs=1` 意味着视频提取和文件导入串行（视频提取时文件导入排队）。
