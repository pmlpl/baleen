# ADR 0008：G2 从「判官」变「编辑」+ 用户上传必须配 API Key

- 状态：**已采纳（2026-11-12 主人拍板）**
- 日期：2026-11-12
- 提出者：ZCode（主人「放宽进入标准 + AI 按契约整理笔记」需求触发）
- 关联：ADR 0002（G0 解析栈）、ADR 0004（切块）、ADR 0006（视觉转写）、note-format.md §7（修正机制）

---

## 1. 背景与痛点

主人导入 B 站视频字幕时，G2 归属闸把课程推广视频拒收了（判为"广告流水话"）。
主人希望：不再因"不是知识"就拒收，而是让 AI 读 note-format.md 契约，把内容整理成合格笔记再入库。
如果全文确实无可整理内容（纯广告），才不入库。

同时：没配 API Key 的用户不应允许上传任何内容（文件/图片/视频），防止未整理的原始内容污染知识库。

## 2. 方案选型

| 路线 | 结论 |
|---|---|
| **G2 从判官变编辑 + 合并 outline** | ✅ **采纳**——LLM 读契约重写整篇 markdown（补标题/去噪/组织结构），同一次调用替代 G2 判断 + 标题整理 |
| 只改标题（保留 outline.py 逻辑） | ❌ 去不了营销话术，主人碰到的课程推广问题解决不了 |
| yt-dlp + whisper 语音识别 | ❌ 重量级依赖，Phase 1 不值得（ADR 0007 已否决） |

**主人决策**：
1. 重写整篇 markdown（能去噪 + 提取知识点）
2. 没配 API Key → 不允许上传（no_llm 从用户 API 去掉）
3. charter 从导入流水线中去掉（API/问卷暂留，等个人中心页接手）

## 3. 设计

```
用户上传(文件/URL) → 必须有 API Key → G0(解析) → G1(挡垃圾) → AI笔记整理 → 入库 → 切块 → 向量化
                                                               │
                                                               ├─ 有可整理内容 → 重写后的 blocks → 入库
                                                               └─ 无可整理内容 → 不入库（B）

chat save-note → no_llm=True(内部) → G0 → G1 → 原文直接入库（已被 LLM 整理过）
```

### 新模块：`app/service/rewrite/organizer.py`

- `organize_note(parse_result, *, filename, ext, client) -> OrganizeResult`
- system prompt 嵌入 note-format.md 关键规则（§2 标题层级 + §3 块类型 + §5 内容骨架）
- LLM 输出 JSON `{organizable, markdown, quality, note_type, reasons}`
- organizable=true → `parse_md_text(markdown)` → `renumber_headings_in_blocks()` → 返回 blocks
- organizable=false → blocks=None，pipeline 不创建笔记
- LLM 异常/JSON 坏 → 降级原文入库（不因整理失败丢内容）

### pipeline.py 变更

- G2 归属闸整段 + 标题整理整段 → 替换为单个 `organize_note` 调用
- review_runs gate="G2" 保留（verdict: accept=已整理 / reject=无可整理内容）
- charter 参数保留但不再传给 organizer
- no_llm=True → 跳过 organizer（chat save-note 内部路径）

### upload.py 变更

- `POST /api/sources`：删除 `no_llm` query 参数
- `POST /api/sources/url`：`UrlImportRequest` 删除 `no_llm` 字段
- 两个端点都必须配 API Key（无 key → 400）

### 保留的文件

| 文件 | 为什么保留 |
|---|---|
| `gate/g2.py` | eval 系统直接调 `judge()`，不受 pipeline 改动 |
| `rewrite/outline.py` | `number_headings` 计数逻辑被 organizer 复用；`backfill.py` 也用 |
| `charter/` | API/问卷暂留，等个人中心页 |

## 4. 已知限制

- organizer 重写整篇 markdown：LLM 可能改坏好内容（降级为 temperature=0 + 导入后可编辑）
- 超长内容截断到 token 预算内（同 G2 现有 truncate 逻辑）
- 断点续跑时 organizer 结果不持久化（与旧 outline.py 同款行为，pre-existing）
- eval 系统仍用旧 g2.judge()，需后续更新为评估 organizer
