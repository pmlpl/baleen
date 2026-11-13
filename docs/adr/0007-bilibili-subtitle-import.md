# ADR 0007：B 站视频字幕导入（纯 HTTP 拉字幕，不引 yt-dlp/whisper）

- 状态：**已采纳（2026-11-12 主人拍板：字幕优先 / 纯 HTTP / 无字幕报错）**
- 日期：2026-11-12
- 提出者：ZCode（主人「视频导出成笔记」需求触发）
- 关联：ADR 0002（G0 解析栈）、ADR 0006（视觉转写——同为「非文档格式 → 文字」路线）

---

## 1. 背景与痛点

笔记库此前只支持文件导入（PDF/DOCX/MD/PPTX/TXT/CSV/图片）。主人希望把 B 站视频
也变成可检索的笔记——教育类视频（课程、技术讲座）是个人知识库的重要来源。

视频「导出成笔记」的核心是拿到文字内容。两条路线：
- **字幕路线**：B 站很多视频有 CC 字幕或 AI 字幕，直接拉字幕 JSON 就能得到完整文字。
- **语音识别路线**：下载音频 → whisper 转写。能覆盖无字幕视频，但依赖重。

## 2. 方案选型

| 路线 | 结论 |
|---|---|
| **纯 HTTP 拉字幕 → markdown → 现有管线** | ✅ **采纳**——零新依赖（urllib 标准库），复用全链路管线，实现最快 |
| yt-dlp 拉字幕 | ❌ ~30MB 第三方依赖，仅为拉字幕过重；yt-dlp 本身是视频下载器，用它拉字幕大材小用 |
| yt-dlp + whisper 语音识别 | ❌ 三个重量级依赖（yt-dlp + ffmpeg + whisper），需 GPU/大量计算；Phase 1 不值得 |
| yt-dlp 下载视频 → 视觉模型逐帧识别 | ❌ 最重路线，视频帧不是文档，视觉模型转写效率低 |

**主人决策**：
1. 只用字幕，无字幕报错（不做 whisper 兜底）
2. 纯 HTTP（urllib 标准库），不加任何第三方依赖
3. 后端 + 前端一起做

## 3. 设计

```
用户粘贴 B 站链接
  → POST /api/sources/url {url, no_llm?}
      → app/service/bilibili.py:fetch_bilibili(url, sessdata=…)
          ① 解析 BV 号（正则 BV[a-zA-Z0-9]{10}）
          ② GET api.bilibili.com/x/web-interface/view?bvid={bvid}
             → 标题/UP主/cid/aid/duration/章节(view_points)
          ③ GET api.bilibili.com/x/player/v2?cid={cid}&aid={aid}（带 SESSDATA cookie）
             → 字幕列表；优先 zh，否则取第一个
          ④ GET {subtitle_url} → 字幕 JSON [{from,to,content}]
          → 拼 markdown：# 标题 / > 元数据 / ## 章节 / 字幕段落
      → 写 markdown 到暂存区 → sha256 → _store_blob
      → create source(ext=.md, parse_status=pending) → enqueue_import
  → worker 跑现有管线：G0(parse_md) → G1 → G2 → 标题整理 → chunk → embed
```

**关键设计点**：

1. **零管线改动**：bilibili.py 只负责「拉字幕拼 markdown」，后续 G0/G1/G2/chunk/embed
   全部复用现有 .md 导入路径。parse/、chunk/、embed/、pipeline.py 一行不改。

2. **sha256 去重**：基于字幕 markdown 内容哈希。同一视频重复导入 → 相同字幕 → 相同哈希 → 去重。
   视频后来新增了字幕 → 哈希变了 → 不去重（正确行为）。

3. **SESSDATA 配置**：B 站 player/v2 接口带 SESSDATA 才能拿到大部分视频的 CC/AI 字幕。
   从 `.env` 的 `BILIBILI_SESSDATA` 读（全局配置，非 per-user）。未配置时部分视频报「没有字幕」
   并提示「可在 B 站开启 AI 字幕后重试」。

4. **字幕合段**：字幕片段按句末标点（。！？）和时间间隔（>3 秒）合并成段落，
   避免一字幕一行太碎（chunker 目标 250–400 字/块，合段后段落正好落在区间内）。

5. **视频章节**：B 站 view_points 作为 heading 分段，让生成的 markdown 有结构，
   后续 LLM 标题整理能进一步优化。

## 4. 否决的备选

| 备选 | 否决理由 |
|---|---|
| yt-dlp 拉字幕 | 新增 ~30MB 依赖（HANDOFF §3：任何第三方依赖需主人批准）；yt-dlp 是视频下载器，仅拉字幕大材小用；B 站 API 变了要等 yt-dlp 更新 |
| whisper 语音识别 | 需 yt-dlp + ffmpeg + whisper 三个重量级依赖；需 GPU 或大量 CPU；实现复杂度高；Phase 1 不值得 |
| per-user SESSDATA（DB 列） | 需 Alembic 迁移；主人是主要使用者，全局 .env 配置已够；日后多用户场景再迁移 |

## 5. 已知限制

- **无字幕视频不可导入**：B 站 UGC 内容很多没有 CC 字幕。报 400 并提示「可在 B 站开启 AI 字幕后重试」。
  未来可补 whisper 兜底（需另提 ADR）。
- **SESSDATA 是全局配置**：多人使用时所有人共享同一个 B 站账号的 SESSDATA。
  需 per-user 时加 `app_settings` 列 + 迁移。
- **B 站 API 变更风险**：B 站接口非公开稳定 API，可能随时变更。代码集中在一个模块
  （`bilibili.py`），变更时只改一处。
- **字幕质量**：AI 字幕可能有识别错误，导入后可在笔记编辑器修正（与图片视觉转写同款 warnings）。
