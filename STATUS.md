# baleen 系统状态速览

> **全系统状态地图**：模块表 / 测试基线 / 已知限制 / 未决问题。★ 新会话或迷路时只读「当前状态速览」一节。
> 文件内代码路径除特别说明外，均相对于 `backend/`。交付块归档在 `backend/delivery/`（见文末索引），格式按 `HANDOFF.md` §4。
>
> 2026-11-07 拆分原因：原文件 1584 行 / 118 KB，速览与全史混在一处，
> 导致「速览说做完了、下面没有交付块」这类漂移查不出来。

---

## 📌 当前状态速览（★ 新会话只读这一节就够，维护者：协调方）

**最后更新**：2026-11-06 · 对话附件/图片 + 笔记渲染 v18;基线 **371 passed**

### 已完成模块

| 模块 | 路径 | 状态 |
|---|---|---|
| **G0 解析** | `app/service/parse/` | ✅ 8 格式(.csv v14 起) + txt 直通；35 样本 smoke 通过 |
| **数据模型** | `app/models/`、`alembic/versions/0002_*` | ✅ 6 表 + `halfvec(1024)` + HNSW + trgm GIN |
| **切块** | `app/service/chunk/` | ✅ 同节内合并；真实笔记 in_range 64%–100% |
| **向量化** | `app/service/embed/` | ✅ torch 2.14.1+cu126，cuda 52 items/s |
| **G1 价值闸** | `app/service/gate/g1.py` | ✅ 真实样本**误拒率 0/15 = 0.0%** |
| **G2 归属闸** | `app/service/gate/g2.py` | ✅ H-批2 + 线 J 政策修正；**live 已跑**（2026-10-31 全量 35 样本，ok 口径误拒率 0/15） |
| **导入状态机** | `app/service/imports/pipeline.py` | ✅ I-批1；端到端实测，父块无向量已核对 |
| **arq worker + 上传 API** | `app/worker/`、`app/api/upload.py` | ✅ I-批2；新建 201 / dedupe 200 / 轮询可用 |
| **混合检索 + RRF** | `app/repo/search.py`、`app/service/retrieve/` | ✅ 线 K；真实 query 双路命中，RRF 与公式吻合 |
| **查询 / 检索 API** | `app/repo/library.py`、`app/api/library.py` | ✅ **线 M**；契约 4 接口 + 真实链路自测 |
| **问答服务** | `app/service/qa/`、`app/api/qa.py` | ✅ **线 Q 批1-7**；多轮会话（0003/0004 + llm_content 重放缓存对齐）+ SSE 流式 + 问题改写 + 会话导出 |
| **设置** | `app/models/setting.py`、`app/api/settings.py` | ✅ **线 Q 批8**；app_settings 单行覆盖 .env，热生效 |
| **API 端点总览** | `app/main.py` | ✅ 26 个：healthz 1 · 认证 3 · 章程 3 · 上传 2 · 笔记/检索 7 · 问答/会话/导出 8 · 设置 2（数字由 `scripts/doc_lint.py` 守门） |
| **前端** | `frontend/` | ✅ 三页 + 问答（流式）/设置页 + 登录注册 + 书架 + 笔记两栏阅读 |
| **多用户认证** | `app/service/auth/`、`app/api/auth.py`、`app/models/user.py` | ✅ 用户名+scrypt+JWT；`owner_id` 从常量改 token 解出；数据按账号隔离 |
| **Charter 问卷** | `app/service/charter/`、`app/api/charter.py` | ✅ 首次登录强制问卷 → LLM 生成章程 → 存 DB → G2 热生效；每用户一份 |
| **标题结构整理** | `app/service/rewrite/` | ✅ 导入时 LLM 出语义标题、代码算编号；超长文档分段；**需非推理模型** |
| **存量回填（线 R）** | `app/service/rewrite/backfill.py`、`scripts/backfill_outline.py` | ✅ 历史 15 篇全部收口（11 rewritten + 4 该跳），与导入同构、幂等闭环 |
| **会话优先前端** | `frontend/` | ✅ 导入/检索并入对话（/导入 文件选择器+拖放、/检索 引用选择器）；删 /import /search 路由页；导航只剩 笔记库/问答/设置 |
| **引用笔记** | `app/api/qa.py note_ids`、`<referenced_notes>` | ✅ 引用=告诉 AI「在问什么」，检索照跑找关联；[引n]/[n] 双标注 |
| **[NO_KB] 兜底** | `answerer.py` + `qa.py` | ✅ KB 答不了不再拒答：联网二次回答或 AI 自身知识（带标注）；防瞎编纪律保留 |
| **切块·代码超限修复** | `app/service/chunk/chunker.py` | ✅ 超硬上限(480 tok)的函数体按行切分(`code_part` 编号),不再被 bge 静默截断;15 篇语料超限 0,20 query 期望路径失配 0 |
| **parse_status=pending** | `0009` 迁移 + 上传/流水线创建点 | ✅ 上传到 G0 之间的窗口不再伪装 `error`;CHECK 加枚举,dev 库已应用,往返验证 |
| **mermaid 截断根因 #2** | `frontend/src/utils/mermaid.ts` | ✅ v12 给顶点标签钳 max-width≈120px + fastdom 异步竞态;`wrappingWidth:500` 修复,主人真实会话 10 标签零裁切(d9ba566) |
| **笔记自编辑** | `app/service/notes_edit.py` + `PUT /api/notes/{id}` + NoteView 编辑模式 | ✅ 标题/markdown 编辑;内容编辑重建切块+向量(同事务);不跑 LLM、回填不覆写;图片块禁改正文;GET 详情 markdown 改 doc 现场序列化 |
| **markdown 死列删除(0010)** | `alembic/versions/0010_*` | ✅ 从未生成过、全表 null,主人拍板删;dev 库已应用+往返验证;API 字段(doc 现场序列化)不受影响 |
| **启动预热嵌入模型** | `app/main.py` + `EMBED_WARMUP_ON_STARTUP` | ✅ 后台线程启动即加载 bge,消除重启后第一问 3~8s 冷启动;真机实证自动加载;测试用例显式关掉 |
| **图片视觉理解(ADR 0006)** | `app/service/parse/vision.py` + `parse_file(client=)` + qa.py `_load_hit_images` | ✅ 导入时视觉模型转写 Markdown 进流水线(降级 no_text_expected);问答命中图片来源笔记回传原图(≤2 张),流式 meta 加 `images_attached`;e2e 全通:识别→G1/G2 accept→检索→看着答 |
| **NoteView mermaid** | `frontend/src/views/NoteView.vue` | ✅ 阅读页 mermaid 代码块渲染成图(mermaid 感知启发式 + renderMermaidIn,失败退化代码块);截图实证 |
| ~~worker LLM 配置统一~~ | — | ✅ 核实早已实现(import_file/summarize 都走 resolve_llm_config),悬账销账 |
| **笔记删除** | `DELETE /api/notes/{id}` + `NotesRepo.delete_active` + 书架/详情删除入口 | ✅ 真删除:chunks/embeddings DB 级联清;孤儿 source 连行带 blob 删(1:N 防护);确认弹窗;7 测试 |
| **CSV 导入** | `app/service/parse/csv_parser.py` + PARSERS | ✅ 整篇表格、首行表头、分隔符嗅探(逗号/分号/制表符/竖线)、GB18030 兼容、>200 行分表;9 测试;xlsx 提示话术成真 |
| **评测候选自动归档(C 路线)** | `app/service/eval_capture.py` + pipeline 接线 | ✅ 真实导入(非 dedupe)完成→原文件副本进 `eval/candidates/`(gitignore)+登记 `eval/candidates_log.csv`;best-effort 不影响导入;`EVAL_CAPTURE_ENABLED` 开关;主人复核后挪 samples 转正 |
| **bootstrap 置信区间(D 路线)** | `eval/scripts/bootstrap.py` + `ci_g12_*.md`/`ci_retrieval.md` | ✅ B=2000/seed=42/百分位法;误拒率 10% CI [0%,33.3%](test)、R@5 94.4% CI [83.3%,100%]——n 小的诚实表述;test/dev 交叉视角;⚠️ q10 语料漂移(rank 12,回填改标题所致,待主人复核) |
| **边界测试批** | `tests/service/parse/test_edge_cases.py` 等 | ✅ 混合 PDF 阈值行为/容器内嵌套图往返(**抓到并修复 2 个真 bug**)/CSV RFC4180/media 孤儿语义钉住/dedupe×附件语义钉住 |
| **含图笔记可编辑** | `notes_edit` 位置标记往返 + `PUT /api/notes/{id}` 解除 400 | ✅ 图片经 `![alt](baleen-image:N)` 往返,保存按 N 重关联原节点;删标记=删图;旧 400 分支移除 |
| **md 带图片导入** | `parse/md_parser.py` + `service/media.py` + `POST /api/sources attachments` + `GET /api/media/{key}` | ✅ 图片按相对路径随主文件上传→内容寻址存 `storage/media/`;doc 只留 `media` key;缺文件退 alt 并警告;外链不抓;暂存改每上传一目录(worker 清理兼容) |
| **扫描件 PDF 视觉识别** | `vision.parse_scanned_pdf` + `parse_pdf(client=)` | ✅ 无文本层 PDF 逐页 150dpi 栅格化→视觉转写;单页失败跳过留痕、全失败降级;30 页上限明说;有文本层 PDF 不烧视觉;e2e 中文两页全通(G1/G2 accept,标题正确)| 
| **SVG 插图** | `frontend/src/utils/svg.ts` + QaView/NoteView 接入 + prompt 引导 | ✅ ```svg 代码块沙箱 iframe 渲染(sandbox 无 allow-* + CSP default-src 'none',零依赖);DOMParser 预量高;失败退化代码块+角标;浏览器截图验证含 script 的 SVG 不执行;存为笔记保留图块 |
| **对话附件（v17）** | `qa.py` `attachments`/`images` + QaView 📎 | ✅ 文本入 `<reference_files>` 不入库；图片走视觉通道（前端缩 1024px）；挂图不搜网；纯文本模型拒图时去图降级；无视觉模型也能用 |
| **笔记渲染（v18）** | `frontend/src/utils/section.ts` | ✅ 表格/mermaid 重建；引用徽章可点（外链+站内）；文末出处清单；`finish_reason` 截断续写兜底 |

### 测试基线

```
cd backend && uv run pytest -q     →  371 passed
```

基线轨迹：37 → 62 → 77 → 87 → 90 → 109 → 118 → 139（线 M）→ 176（线 Q 批1-8）
→ 183（阶段3/4+工具链）→ 199（多用户）→ 228（标题整理 + 认证/隔离）
→ 247（引用笔记 + [NO_KB] 兜底 + 线 R 回填测试）→ 250（代码超限切分 + parse_status=pending）
→ 263（笔记自编辑:序列化往返 7 + 端点 6）
→ **274**（视觉解析 7 + 问答回传原图 4;2026-11-01）
→ **290**（笔记删除 7 + CSV 解析 9;2026-11-02）
→ **301**（扫描件 PDF 视觉 11;2026-11-02）
→ **320**（md 图片导入 8 + media 端点 2 + 含图编辑改写 2 + 附件上传 3;2026-11-02）
→ **334**（候选归档 7 + 边界测试批 7;2026-11-03）
→ **340**（对话文本附件 6;2026-11-05）
→ **345**（图片附件 3 + 带图优雅降级 2;2026-11-05）
→ **346**（挂图不搜网 1;2026-11-05）
→ **366**（引用重编号/出处清单 11 + finish_reason 2 + 截断续写 7;2026-11-05）
→ **371**（笔记渲染 table/mermaid 修复;2026-11-06）

**任何新任务都不许把这个数降下来。**

### 累计的已知限制（跨交付汇总，别重复踩）

1. ~~切块参数 300/250/400/480 是推导值~~ → 2026-10-31 已消融校准（维持 300，见 eval/ablation.md §12）；**代码块超 480 tok 静默截断也已修复**（按行切分）
2. **G1 的 quality 恒为 core，无区分度** —— 质量分级须由 G2 决定
3. **embeddings.vec 是 HALFVEC(1024)** —— 换不同维度的模型必须**新建表**，不能改配置
4. `.doc` / `.xlsx` / `.xls` **不支持**（显式拒绝+转换提示；`.csv` 已支持）；纯图片与**扫描件 PDF** v13/v15 起走视觉转写（需配置支持视觉的模型，**推理型网关模型不行**）
4a. **评测集语料漂移**：线 R 回填改了标题结构 → q10 的 expected_heading_path 失配(rank 12)——query 期望要不要跟随更新待主人复核;数字引用以 `eval/ci_*.md`(带 CI)为准
4b. **docx/pptx 内嵌图片只有占位节点、没有像素**（G0 从未提取字节）——阅读页显示为 `[图片] alt` 文字；
   **md 导入的图片才有真实字节**（v16 内容寻址存 `storage/media/`）。要补齐 docx/pptx 需另做「提取内嵌媒体」增量
5. **所有单测都用合成 fixture**，真实样本只在 `scripts/*_smoke.py` 里验 —— 真实数据曾抓出「27 测试全绿但 in_range 只剩 11.6%」
6. **问答优先用库内内容**（防瞎编，刻意选择）；库内答不了时走 `[NO_KB]` 兜底——联网搜索（Tavily）或 AI 自身知识二次回答，带标注；追问检索脱靶由问题改写缓解（线 Q 批6）
7. **API Key 不继承 .env**（多用户决策）：每个账号必须自己配，没配时上传会 400
8. **标题整理需要非推理模型** —— 推理模型（sensenva 等）会把 max_tokens 全用于 reasoning，
   `content` 返回空；实测 deepseek-flash 正常。这是模型特性，不是代码问题
9. **标题整理只作用于新导入** —— 存量已由线 R 回填脚本一次性收口（15 篇：11 rewritten + 4 该跳），幂等；新导入仍自动整理

### 累计的未决问题

| 状态 | 事项 |
|---|---|
| ✅ 已定 | GPU torch（cu126） |
| ✅ 已定 | `.env` 放根目录 + `config.py` 用绝对路径读 |
| ✅ 已定 | `pending_ocr` 归 `sources.parse_status`，不进 `review_runs.action` |
| ✅ 已完成 | **G2 真跑评测**（2026-10-31，deepseek-flash 全量 35 样本，ok 口径误拒率 **0/15**） |
| ✅ 已闭环 | 槽 7 分层细化到 (槽位, 判定)：test 18/18 覆盖（2026-11-02 复核） |

### 环境要点

- **Docker 必须跑**：`docker compose ps`（db + redis）
- **模型路径**：本地 `bge-large-zh-v1.5`（约 1.21 GB，**不在仓库内**），路径由 `.env` 的 `EMBEDDING_MODEL_PATH` 指定
- **配置**：`.env`（从 `.env.example` 复制；**已 gitignore，永不提交**）
- **临时服务器清理**：见 `HANDOFF.md` §3.1（必须 `taskkill /T`，曾留下 17 小时的孤儿 uvicorn）
- **LLM 上游额度（实测教训，重要）**：
  `LLM_BASE_URL` 走本机内网网关 → 上游账号池。
  ⚠️ 上游账号全部 `insufficient_quota` 时，网关把 `all_upstreams_failed` 汇总成
  **HTTP 502（不是 429）** → **重试无用**，只能等额度恢复或加账号，**别误判成代码 bug**。
  ⚠️ 网关定期探测 degraded 账号，**本身也在消耗额度**。
  ⚠️ `glm-5.2` 是**推理模型**：`max_tokens=8` 时 `content` 为空、`finish_reason=length`，
  思考进 `reasoning_content`；`probe()` 用的正是 `max_tokens=8`，**可能把推理模型误判成
  chat 不可用**（待额度恢复后验证）。正常调用 `max_tokens=800` 实测 **3.4 秒/次**，JSON 正常。
- **2026-10-30 更新**：`LLM_MODEL=glm-5.2`（主人切换，现走设置页可热改）；**arq worker 必须单独启动**
  （`uv run arq app.worker.WorkerSettings`，`start.bat` 已带）——没起 worker 导入永远「排队中」；
  问答/改写走 SSE 流式（`POST /api/qa/chat/stream`），契约见 `docs/api.md` §5。

---

## 📂 交付记录在哪（2026-11-07 拆分）

速览只讲「现在是什么样」；历史交付块按阶段归档，不再堆在这一个文件里。

| 归档文件 | 覆盖 |
|---|---|
| `delivery/2026-10-foundation.md` | 线 B 数据模型 + G0 补遗 · E / E-2 切块与向量化 · F 配置加载 |
| `delivery/2026-10-gates-retrieval.md` | G G1 · H-批1 LLM adapter · H-批2 G2 · J 政策修正 · I-批1 导入状态机 · I-批2 worker/API · K 混合检索 · M 查询 API |
| `delivery/2026-10-qa-and-eval.md` | Q-批1~10 问答/流式/设置/导出 · R 存量回填 · G2 真跑 · 切块档位重跑 |

⚠️ **2026-11-01 起的交付没有独立交付块**（视觉解析、多用户认证、charter 问卷、标题整理、笔记编辑/删除、
CSV、扫描件 PDF、md 图片、SVG 插图、对话附件、v18 渲染）。这些只在上面「已完成模块」表里有名字，
明细看 `git log` 与 `../日志/2026-11-0*.md`。

> 这是历史欠账，不是新增的规矩：**从下一次交付起，仍按 `HANDOFF.md` §4 的格式写块**，
> 写进对应阶段的归档文件，不再往速览下面追加。

---


