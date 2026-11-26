# baleen 系统状态速览

> **全系统状态地图**：模块表 / 测试基线 / 已知限制 / 未决问题。★ 新会话或迷路时只读「当前状态速览」一节。
> 文件内代码路径除特别说明外，均相对于 `backend/`。交付块归档在 `backend/delivery/`（见文末索引），格式按 `HANDOFF.md` §4。
>
> 2026-11-07 拆分原因：原文件 1584 行 / 118 KB，速览与全史混在一处，
> 导致「速览说做完了、下面没有交付块」这类漂移查不出来。

---

## 📌 当前状态速览（★ 新会话只读这一节就够，维护者：协调方）

**最后更新**：2026-11-26（速览格式重构：流水账下沉 `backend/delivery/2026-11.md`「速览归档」节，本表每批一行）

| 日期 | 批次 | 一句话 | pytest |
|---|---|---|---|
| 2026-11-26 | 画像页 v41（左右布局+八块统计） | 章程页扩成「我的画像」：左栏五项事实可原地编辑（迁移 0021 补存），右栏八块真实统计（互斥三分口径）+ 两阶段主题打标写 notes.tags，api.md v41 | 713 |
| 2026-11-26 | 问卷事实化+昵称问答称呼 | 旧问卷偏好题→六事实题（昵称/年龄段/性别/学历/状态/为什么用），偏好由 LLM 推导；迁移 0020 昵称注入问答 system prompt，api.md v40 | 704 |
| 2026-11-26 | 书架布局五连修 | 翻页钉底+宽度固定+书封按视口高自适应（纯 CSS） | 702 |
| 2026-11-26 | 个人中心整页下线 | v38 收缩到两项 → v39 删页删端点；端点 35→34，api.md v39 | 702 |
| 2026-11-25 | 纸篓软删除落地 | archived_at+30 天 cron 清理+restore/purge 只认 archived，api.md v37 | 708 |
| 2026-11-25 | 会话重命名 PATCH | api.md v36，不刷 updated_at 位次不动；CORS 补 PATCH | 699 |
| 2026-11-25 | UI 体验补齐 | markmap 思维导图/灯箱拖拽/侧栏 248px/确认框统一 | 696 |
| 2026-11-24 | 审查报告 B/C 级落地 | B3 上传魔数判头+B5 CORS 收紧+C1 决策树抽共享+C2 拆组件 | 696 |
| 2026-11-24 | 审查报告 B/C 级裁决 | B1 登录限流/B2 media 鉴权/B4 jwt_secret descope（毕设不公网部署，部署前必须重启，见 `docs/deployment-checklist.md`） | 683 |
| 2026-11-23 | 审查报告 A 级修复 | MAX_MEDIA_BYTES 真闸+llm client docstring 对齐 | 683 |
| 2026-11-23 | 联网搜索两级链 | Tavily 任何失败自动降级 DeepSeek Search，降级留痕 | 680 |
| 2026-11-23 | 负例集创建批 | 负例 8→25 条，preflight v2 三段式 | 668 |

测试基线 **713 passed**（+1 xfailed）。每批完整交付说明 → `backend/delivery/`（2026-11 月见 `2026-11.md`）；更早基线轨迹见该文件交付块。

**backlog #1/#3/#6 全部落地(2026-11-19 深夜续)**：#3 渲染回归探针 35 断言(探针当天抓出 `looksLikeCode` 把 `**粗体**` 开头段落吞成代码的真缺陷,400 父块对拍只 4 处变化全是修复)+ g2_eval 视觉通道修复(9 条 G0 假缺口)+ #6 帧选择评测(16 条题集,首跑 5 FAIL 全对应主人裁决)+ #1 驳回样本筛选工具(发现一个三方标注分歧待裁决)

### 已完成模块

| 模块 | 路径 | 状态 |
|---|---|---|
| **G0 解析** | `app/service/parse/` | ✅ 8 格式(.csv v14 起) + txt 直通；35 样本 smoke 通过 |
| **数据模型** | `app/models/`、`alembic/versions/0002_*` | ✅ 6 表 + `halfvec(1024)` + HNSW + trgm GIN |
| **切块** | `app/service/chunk/` | ✅ 同节内合并；真实笔记 in_range 64%–100% |
| **向量化** | `app/service/embed/` | ✅ torch 2.14.1+cu126，cuda 52 items/s |
| **G1 价值闸** | `app/service/gate/g1.py` | ✅ 真实样本**误拒率 0/15 = 0.0%** |
| **G2 归属闸** | `app/service/gate/g2.py` | ✅ H-批2 + 线 J 政策修正；eval 系统用（pipeline 改用 organizer，见下） |
| **AI 笔记整理（v21）** | `app/service/rewrite/organizer.py` + `pipeline.py` | ✅ 替代 G2+outline：LLM 读 note-format.md 契约重写 markdown（补标题/去噪/组织结构）；无可整理→不入库；LLM 异常→降级原文入库；没配 key 不允许上传；**organizer 重写后首 H1 回写 notes.title(`_title` 去编号),不再卡在原文派生值——修视频导入用平台文案当标题的 blob(4986 已修)**；**预算自适应(v22):去掉过时硬编码(PROMPT_TOKEN_BUDGET=6000/llm_max_output_tokens=8192)→ app_settings 加 llm_context_tokens/llm_max_output_tokens(用户填,没填默认全文喂不截断+131072 输出兜底),修长视频/长文档导入正文被截到几千字** |
| **导入状态机** | `app/service/imports/pipeline.py` | ✅ I-批1；端到端实测，父块无向量已核对 |
| **arq worker + 上传 API** | `app/worker/`、`app/api/upload.py` | ✅ I-批2；新建 201 / dedupe 200 / 轮询可用 |
| **混合检索 + RRF** | `app/repo/search.py`、`app/service/retrieve/` | ✅ 线 K；真实 query 双路命中，RRF 与公式吻合 |
| **查询 / 检索 API** | `app/repo/library.py`、`app/api/library.py` | ✅ **线 M**；契约 4 接口 + 真实链路自测 |
| **问答服务** | `app/service/qa/`、`app/api/qa.py` | ✅ **线 Q 批1-7**；多轮会话（0003/0004 + llm_content 重放缓存对齐）+ SSE 流式 + 问题改写 + 会话导出 |
| **设置** | `app/models/setting.py`、`app/api/settings.py` | ✅ **线 Q 批8**；app_settings 单行覆盖 .env，热生效；**v22 加 llm_context_tokens/llm_max_output_tokens(per-user 预算,没填走兜底)** |
| **API 端点总览** | `app/main.py` | ✅ 36 个：healthz 1 · 认证 3 · 画像 5（v41：章程 3 + 统计 1 + 主题打标 1）· 上传 3 · 笔记/检索 10（v37 加纸篓 3 个）· 问答/会话/导出 10（v36 加重命名）· 解析 1 · 设置 2 · B站登录 2（数字由 `scripts/doc_lint.py` 守门） |
| **前端** | `frontend/` | ✅ 三页 + 问答（流式）/设置页 + 登录注册 + 书架 + 笔记两栏阅读 |
| **多用户认证** | `app/service/auth/`、`app/api/auth.py`、`app/models/user.py` | ✅ 用户名+scrypt+JWT；`owner_id` 从常量改 token 解出；数据按账号隔离 |
| **Charter 问卷 + 画像** | `app/service/charter/`、`app/api/persona.py` | ✅ 首次登录强制问卷（v40 事实化：六事实题，偏好由 LLM 推导）→ LLM 生成章程 → 存 DB → 导入整理热生效；每用户一份；昵称（迁移 0020）随保存落库，问答 system prompt 注入称呼；**v41 扩成「我的画像」页**：左栏五项事实可原地编辑（迁移 0021 补存），右栏系统统计（纯只读现算）+ 主题打标写 `notes.tags` |
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
| **笔记删除** | `DELETE /api/notes/{id}` + `NotesRepo.delete_active` + 书架/详情删除入口 | ✅ 真删除:chunks/embeddings DB 级联清(ON DELETE CASCADE,实测孤儿向量/切块=0);孤儿 source 连行带 blob 删(1:N 防护);确认弹窗;7 测试;**书架删除乐观移除(API 成功后本地 filter+total-1,不重 load,瞬时生效不闪);LibraryView keep-alive 加 onActivated 兜底(详情页删完跳回不再 stale)** |
| **CSV 导入** | `app/service/parse/csv_parser.py` + PARSERS | ✅ 整篇表格、首行表头、分隔符嗅探(逗号/分号/制表符/竖线)、GB18030 兼容、>200 行分表;9 测试;xlsx 提示话术成真 |
| **评测候选自动归档(C 路线)** | `app/service/eval_capture.py` + pipeline 接线 | ✅ 真实导入(非 dedupe)完成→原文件副本进 `eval/inputs/candidates/`(gitignore)+登记 `eval/inputs/candidates_log.csv`;best-effort 不影响导入;`EVAL_CAPTURE_ENABLED` 开关;主人复核后挪 samples 转正 |
| **bootstrap 置信区间(D 路线)** | `eval/scripts/bootstrap.py` + `ci_ok_*.md` | ✅ **比例类指标一律 Clopper-Pearson 精确区间**（误拒率/误丢率/话题召回率/判定准确率/Recall@k）；macro-F1、MRR 仍用 B=2000/seed=42 百分位 bootstrap。**2026-11-19 修**：百分位法在零事件时报 `[0%,0%]`（宣称「必然是 0」，不成立）、全事件顶 `[100%,100%]`——统一改 CP 后 0/47 → `[0%,7.5%]`、35/35 → `[85%,100%]`；新增 `--ok-only` 与 metrics.py 同口径；15 测试（4 条验过「退回旧行为即 FAIL」） |
| **边界测试批** | `tests/service/parse/test_edge_cases.py` 等 | ✅ 混合 PDF 阈值行为/容器内嵌套图往返(**抓到并修复 2 个真 bug**)/CSV RFC4180/media 孤儿语义钉住/dedupe×附件语义钉住 |
| **含图笔记可编辑** | `notes_edit` 位置标记往返 + `PUT /api/notes/{id}` 解除 400 | ✅ 图片经 `![alt](baleen-image:N)` 往返,保存按 N 重关联原节点;删标记=删图;旧 400 分支移除 |
| **md 带图片导入** | `parse/md_parser.py` + `service/media.py` + `POST /api/sources attachments` + `GET /api/media/{key}` | ✅ 图片按相对路径随主文件上传→内容寻址存 `storage/media/`;doc 只留 `media` key;缺文件退 alt 并警告;外链不抓;暂存改每上传一目录(worker 清理兼容) |
| **扫描件 PDF 视觉识别** | `vision.parse_scanned_pdf` + `parse_pdf(client=)` | ✅ 无文本层 PDF 逐页 150dpi 栅格化→视觉转写;单页失败跳过留痕、全失败降级;30 页上限明说;有文本层 PDF 不烧视觉;e2e 中文两页全通(G1/G2 accept,标题正确)| 
| **SVG 插图** | `frontend/src/utils/svg.ts` + QaView/NoteView 接入 + prompt 引导 | ✅ ```svg 代码块沙箱 iframe 渲染(sandbox 无 allow-* + CSP default-src 'none',零依赖);DOMParser 预量高;失败退化代码块+角标;浏览器截图验证含 script 的 SVG 不执行;存为笔记保留图块 |
| **对话附件（v17→v28）** | `app/api/parse.py` + `service/qa/file_analyzer.py` + `service/parse/html_parser.py` + QaView 📎 | ✅ v28 起 📎 文档类统一走 `POST /api/parse`（复用 G0 不入库，HTML/docx/pdf/pptx/md/csv/txt 一套口径）；正文 > 40000 字符 map-reduce 分层分析（`mode=analyzed`，20 块上限→422）；`attachments[].text` 上限 20000→40000；图片走视觉通道（前端缩 1024px）；挂图不搜网；纯文本模型拒图时去图降级 |
| **冻结分段摘要 digest（v28）** | `app/service/qa/digest.py` + `answerer.py`(`HISTORY_TOKEN_BUDGET`) | ✅ 老对话压成不可变「第二条 system」+ 近窗 append-only，**保前缀缓存**、止静默丢失、砍二次式成本；token > 100K 触发 / 200K 安全网；异步 arq 触发，旧消息不删、前端零改 |
| **问答 agent harness（L1，ADR 0010）** | `app/service/qa/agent.py` + `llm/client.py`(tools 透传) + `api/qa.py`(接线) | ✅ answerer 决策树上翻 tool-call loop：脑选工具（search_kb 换词再搜 / search_web 何时转联网 / 直接答），预算三道闸+出口校验在马具（超了回落决策树，最坏=现状）；**agent 默认开**（主人验收拍板，迁移 0017；false=显式关回旧决策树留作紧急开关/A-B，api.md v32）；`qa_messages.trace` 每步留痕（迁移 0016）；负例两档 8/8 PASS，agent 档判据精细化待主人复核；前缀缓存实测不断（agent 62.4% vs 关 56.7%）；**前端零改动** |
| **会话侧栏布局 + 保留策略（v33）** | `QaView.vue` + `repo/qa.py`(touch/trim) + `api/qa.py`(两条流接线) + 迁移 0018 | ✅ 布局：侧栏外壳 `position:relative` + 内容绝对铺满（`inset:0`）+ 列表 `flex:1;min-height:0;overflow-y:auto`——侧栏不参与行高，两栏**像素级同高各自内滚**，「+ 新对话」固定顶部；不引视口魔数（topbar/页头高度多变 calc 必脆）。保留策略：`qa_sessions.updated_at`（迁移 0018，存量回填 = created_at，老用户零感知）落一条消息即刷、仅查看不刷；列表按最近活跃排（旧会话发一轮上浮顶部）；每用户只留最新 50 个（**新建会话时触发硬删**，消息/摘要 FK CASCADE 连带删，删几个记日志）；前端 404 优雅降级（被裁会话旧标签页发消息→已开新对话+提问还回）。Playwright 探针（60 vs 5 会话页高同为 800px，修复前 2788px）+ 识图智能体三截图双验；**+3 测** |
| **笔记渲染（v18）** | `frontend/src/utils/section.ts` | ✅ 表格/mermaid 重建；引用徽章可点（外链+站内）；文末出处清单；`finish_reason` 截断续写兜底；**格式渲染（2026-11-19）**：父块围栏代码块/列表/粗体/引用（chunker 父块 display 序列化 + 围栏/列表/引用/行内渲染,老笔记行为不变）;插图限宽 640px+点击灯箱;**插图按宽高比自适应（方案 C,主人拍板）**：`utils/figure-layout.ts` 在 img load 后按 naturalWidth/Height 分类——窄图(w/h ≤ 4/3,如 6015 的 1080×1920 竖屏帧)左浮动小图文字环绕,宽图(16:9 视频帧)保持居中限宽 640px,900px 以下回落单栏居中。**渲染回归探针（2026-11-19）**：`scripts/probe_section_render.mjs` 35 条断言钉住 6 个渲染分支+老笔记兼容+XSS 底线（esbuild 打包到 node 跑，与 math 探针同范式）；**探针当天就抓出一个真缺陷**——`looksLikeCode` 的注释规则 `/^\s*(\/\/|\/\*|\*)/` 把**任何以 `*` 开头的行**都判成代码，导致「以粗体开头的段落」整段被吞进 `<pre>`；400 个真实父块对拍，4 个变化全是修复（6096 两段误判代码恢复为正文+粗体），零副作用 |
| **数学公式渲染** | `frontend/src/utils/math.ts` + `markdown.ts` | ✅ KaTeX 行内 `$...$`/块级 `$$...$$`,NoteView/QaView 共用;原文级占位保护(公式里 `[1]`/`**` 不被行内替换吃);pandoc 式启发式防美元价签误判;块级收集不越空行/标题/自闭合行(防吞正文成烂公式报红);20 边界用例探针(`scripts/probe_math_render.mjs`,含 QA 真实消息回归)全过 |
| **B 站视频字幕导入（v20）** | `app/service/bilibili.py` + `POST /api/sources/url` + LibraryView URL 对话框 | ✅ 纯 HTTP(urllib)拉 CC/AI 字幕→markdown→现有管线；无字幕报错；SESSDATA 全局配置；有章节分 heading、字幕合段；34 测试 |
| **视频笔记页 + whisper（v22→v26）** | `app/service/video_extractor.py` + `douyin_extractor.py` + `VideoView.vue` | ✅ whisper 异步（API 立即 201，worker 2h 超时，video_stage/progress 进度）；**faster-whisper small**（GPU float16 实测 15x 实时，构造失败/推理失败双重退 CPU int8）；B 站 yt-dlp 路径；**抖音 Playwright 免 cookie**（系统 Edge 拦截详情接口，用户零配置）；前端进度条（whisper 阶段按视频时长插值） |
| **视频→笔记升级·真嵌图（v27→v28）** | `video_extractor.py` + `organizer.py` + `pipeline.py` + `worker.py` | ✅ **PySceneDetect 场景抽帧**(threshold=27 HSV)每场景中点 + **dHash 去重**(裁底部25%——字幕逐句变会把同一白板判成多帧,实测距离8→1) + 上限30帧。**DeepSeek-flash 视觉逐字转录**(保留缩进/符号;格式失败用纯描述 prompt 重试)+帧字节 `store_media` **真嵌图** `![alt](baleen-image:N)`。**organizer 视频多模态**:标记不删/不汇总文末/图随章走 + 标记→画面对照表 + 帧图 base64;输出按帧清单还原 image 块。worker 写 `<md>.frames.json` sidecar,pipeline G0 读它还原。**v28 质量修正(主人 22 分钟真实视频反馈)**:① **帧信息密度筛选** `INFO: high|low`——low(表情包/黑屏/纯标题卡/转场/纯人物特写)丢弃**不落盘**,实测 30→22 张,丢的正是挠头表情包/建议收藏爱心/4张纯标题卡;② **标题层级硬约束**——总标题不当章、**10+ 条目清单禁止一条一章**(显式覆盖「3+大主题必须多章」),H1 **17→4~5**、H2 31→17~19、H3 0→31~39,15 热词归组为基础层/连接层/执行层/业务层;③ **图片扎堆规则**——同小节≤2张、连续3+张须分散(改前 6 张连甩在导读,改后各节 1~2 张、图跟内容走);**493 passed**|
| **视频导入白盒日志（v27）** | `pipeline.py` + `worker.py` + `upload.py` + `sources.ts` + `VideoView.vue` | ✅ `GET /api/sources/{id}` 响应新增 `video_log`（提取+整理全程 `{ts,stage,pct}` 数组，非视频导入为 null）；worker `_update_video_stage`/`_fail_video_job` 每次埋点追加（去重守卫：同 stage 只刷新 pct；截断保留最近 50 条）；pipeline G0 后追加「解析完成」、organizer 前后追加「AI 整理笔记中」/「AI 整理完成」（`SourcesRepo.get_parse_meta` 绕 ORM 缓存读最新值，顺带修 `pipeline.py` headings_rewritten 覆盖过时 parse_meta 的隐患）；前端 `.progress-card` 内滚动日志区渲染时间戳+阶段+百分比；复用 2s 轮询+`parse_meta` 持久化，刷新页面日志仍在；**497 passed** |
| **B站扫码登录 SESSDATA（v30）** | `bilibili_login.py` + `bilibili_extractor.py` + `video_extractor.py` + `worker.py` + `api/bilibili.py` + `VideoView.vue` | ✅ 被风控且未配置/过期时**自动弹真浏览器扫码**(hover 登录入口→立即登录→二维码,探针 v4 实测链路)→抓 SESSDATA 落 app_settings(DB 热生效,迁移 0015)→原地重试兜底;视频页手动按钮+状态(uid+上次扫码);`resolve_bilibili_sessdata` DB 优先 .env 回退;红线=SESSDATA 只进 Playwright 上下文不做 HTTP 重放;12 测;**598 passed** |
| **生成式结构图（有依据才生）** | `app/service/rewrite/diagram_basis.py` + `organizer.py` + 设置页 | ✅ 主人 2026-11-19 拍板「全局默认开,但必须有依据,不然怕 AI 乱生图」——**不靠 LLM 自觉**:代码先算结构依据(并列清单:同级标题≥5 个且每节≤600 字;流程阶段:≥3 种顺序词+≥3 个阶段标题),命中才把「必须用到的节点清单(**全部来自原文标题**)」注入 prompt(没命中就完全不提画图),产出后校验节点 ≥50% 能溯源否则**整块摘掉**并在 reasons 留痕;`app_settings.allow_diagrams`(0014,默认 true)+设置页复选框;17 测试(13 判定 + 4 端到端),验过「撤掉校验即 FAIL」。⚠️仅新导入生效,存量笔记不自动变好 |
| **联网搜索两级链（2026-11-23）** | `app/service/qa/websearch.py` + `qa/agent.py`、`api/qa.py`(全部 7 处调用点接线) | ✅ Tavily 主力(免费 1000 次/月)+**DeepSeek Search 兜底**(付费,owner 自己的 llm key 同网关):Tavily 任何失败(HTTP 432 配额/超时/key 失效)自动降级 `POST {base}/v1/tools/search`(探针实测格式;base_url 归一化带与不带 /v1);`results[].summary` 为主内容字段映射到现有 `{title,url,content}`` 口径,answerer/[W n]/web_context **零改动**);**Tavily 成功永远优先=配额恢复后零配置自动回归免费主力**;降级留痕:logger 告警+agent 步骤 result 与流式 status(响应可见处,api.md 不动=契约无变化);非 deepseek LLM 用户(网关不含 deepseek/未配 key)兜底不可用→Tavily 失败直接自答;agent+两处决策树流内路径同享;10 单测+agent 侧 2 测 |
### 测试基线

```
cd backend && uv run pytest -q     →  713 passed, 1 xfailed（2026-11-26 画像批实测;704→713,+9[五项事实存取 3 测/统计三分互斥与形状与隔离 3 测/主题解析过滤 3 测]）
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
→ **423**（B站字幕+G2编辑器+whisper;2026-11-12）
→ **434**（whisper 异步化;2026-11-13）
→ **450**（抖音视频支持;2026-11-13）
→ **455**（抖音实战排障:dedupe语义+cookies分级校验;2026-11-14）
→ **460**（抖音免cookie提取+进度条+评级/多章指引;2026-11-14）
→ **461**（faster-whisper 迁移+small 模型+帧间隔自适应;2026-11-14）
→ **467**（个人中心聚合端点 6 测:统计口径/owner 隔离/章程截断/密钥不泄漏;2026-11-14）
→ **468**（导入自动建会话撤回:ADR 0005 阶段3,删 summarizer 导入路径;2026-11-15）
→ **491**（视频→笔记升级:场景抽帧+dHash去重+真嵌图+organizer多模态;2026-11-16）
→ **493**（视频质量修正:帧信息密度筛选+标题层级硬约束+图片扎堆规则;2026-11-16）
→ **497**（视频导入白盒进度日志 video_log:提取+整理全程滚动日志区;2026-11-16）
→ **515**（问答页五连修+📎附件统一后端解析与大文件分层分析;2026-11-17）
→ **532**（冻结分段摘要digest:token触发100K+安全网200K,保前缀缓存止静默丢失;2026-11-17）
→ **538**（复核批六连修:父块 display 序列化 3+帧知识相关性/双候选/零帧警告 2+worker 幂等守卫 1;2026-11-19）
→ **539**（晚间小修:重复帧硬守卫(organizer 复制标记→同 media key 只留第一个) 1;2026-11-19 晚）
→ **541**（dedupe 假阳性修复:删笔记重导被旧 done job 静默跳过→source_created 判据重置重跑,2 条真 DB 回归(撤修复即 FAIL);2026-11-19 深夜）
→ **558**（插图方案 C 按宽高比自适应 + 生成式结构图「有依据才生」闸 17 测;2026-11-19 深夜）
→ **573**（bootstrap 零事件区间退化修复:比例指标统一改 CP 精确区间 15 测;2026-11-19 深夜）
→ **575**（backlog#3 渲染回归探针 35 断言 + g2_eval 视觉通道接线 2 测;2026-11-19 深夜）
→ **585**（backlog#6 帧选择质量评测:题集 16 条期望 + 判定逻辑 10 测;2026-11-19 深夜）
→ **598**（B站扫码登录半自动获取 SESSDATA:qr_login 3 路+login_cb 重试链 4+API 5+worker cb 1 共 12 测;2026-11-20 凌晨）
→ **629**（问答 agent harness L1:LLM client tools 透传 6+agent loop 单测 20+API 级灰度 5;2026-11-20）
→ **631**（agent 默认开:迁移 0017+默认开/无设置行专项 2 测;2026-11-20）
→ **641**（会话侧栏布局+保留策略:排序+touch / 52→50 级联删+两条流接线+50 边界 / owner 隔离 3 测,存量 2 断言改 retention-aware;2026-11-20。注:含 agent 批未提交 7 测,若其 WIP 撤回则为 634）
→ **641**（agent 流式批同日叠加 +7:client tools 流式 3+run_agent_iter 流式 4;api.md v34;与 v33 批并发共工作区,叠加态全量 641 全绿;2026-11-20）
→ **651**（质检修正批+追加批:数据修正批不动基线;追加批 serializer 往返恒等 9 测[三坑:粗体 trapped close/字面下划线/嵌套缩进]+1 xfail[标点开头粗体紧邻正文=已知限制];2026-11-22）
→ **668**（转写治本批:transcript_fix 15 测[turbo/None 参数断言+校正应用/降级/skipped/3万封顶/留痕]+worker merge 1 测;1 xfail 照旧;另有 1 项 QA 失败与本批无关 stash 实锤;2026-11-22）
→ **668**（负例集创建批:eval 侧 0 新测,全量复核实测 668 passed+1 xfailed[QA 断言精确化后];2026-11-23）
→ **680**（联网两级链批:websearch 两级链 10 测[成功不降级/432 降级映射+留痕/两级皆败抛/归一化三态/非 deepseek 不可用×3/未配 key/空结果即成功/key 不进日志]+agent 降级留痕 2 测[步骤 result+流式 status];2026-11-23）
→ **683**（审查报告 A 级修复批:store_media 启用 MAX_MEDIA_BYTES 单张 20MB 上限+MediaTooLargeError 降级链[md 退 alt/HTML 退 alt+分类计数]+快照式 3 测[拒收不落盘/边界不误伤/md 与 html 各一条降级];client.py docstring 与 DEFAULT_MAX_RETRIES=5 对齐;2026-11-23）
→ **691**（B3+B5:上传魔数判头 4 测[改名 exe 415/纯文本 docx 415/真文件不受影响/附件 415 整目录清理]+CORS preflight 4 测[放行回显/PATCH 400/非白名单 Origin 400/未列头 400];2026-11-24）
→ **696**（C1:chat/chat_stream 等价性测试 5 例[同输入双端点断言最终答案/引用/used_web/检索调用序列一致+SSE 事件序契约],先写先绿再抽 _decision_tree_answer 共享生成器;2026-11-24）
→ **699**（会话重命名 PATCH /sessions/{id}:改名生效/重命名不浮顶[updated_at 只认落消息]/404+422 校验 3 测;2026-11-25）
→ **708**（纸篓软删除 v37:DELETE 变移篓 status='archived'+trash 列表/restore/purge 三端点+30 天 arq cron 清理+重导三分类判据,+9 测;2026-11-25）
→ **710**（v38 profile 收缩到 user+has_charter 两项,测试重写 8 个净 +2[其中 3 个守住「字段不许回退」];2026-11-26）
→ **702**（个人中心整页下线 v39:GET /api/profile 随唯一消费方一起删,8 测全删;2026-11-26）
→ **713**（画像 v41:五项事实存取/缺省不动/剥空白 3 测 + 统计三分互斥与形状与 owner 隔离 3 测 + 主题解析过滤自创标签与幻觉 id/围栏 JSON 容错/坏 JSON 不抛 3 测;2026-11-26）

**任何新任务都不许把这个数降下来。**

### 累计的已知限制（跨交付汇总，别重复踩）

1. ~~切块参数 300/250/400/480 是推导值~~ → 2026-10-31 已消融校准（维持 300，见 eval/analysis/ablation.md §12）；**代码块超 480 tok 静默截断也已修复**（按行切分）
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
| ✅ 已完成 | **G2 真跑评测**（2026-10-31，deepseek-flash 全量 35 样本，ok 口径误拒率 **0/15**）；**71 条金标准 ok 口径正式报告**（2026-11-19）：test 留出集 **0/17 = 0%**（主结果）、全量 71 条 1/48 = 2.1%（那 1 条经复核为标签错误：内容是自述测试文件，章程明确不收）——报告在 `eval/runs/report_ok_71.md`+`report_ok_test.md`，论文解读在 `eval/analysis/report_ok_71_解读.md`；`metrics.py` 新增 `--ok-only` 参数（剔除 G0 解析失败，7.8%→2.1% 的差异全由 G0 挡的 3 条 `.doc`/图片造成） |
| ✅ 已闭环 | 槽 7 分层细化到 (槽位, 判定)：test 18/18 覆盖（2026-11-02 复核） |
| ✅ 已闭环 | **organizer 多章指引未触发**（2026-11-14）——根因：任务描述「按一个主题一节组织」与多章规则直接冲突，模型听前者。修复：prompt 新增【单章还是多章】块（硬性规则 + 正/误大纲示例 + 并列/递进判断标准），冲突句改为指向该块；真 LLM 双探针验证——五种并列技术→6 章 ✅、TCP 拥塞控制单主题→保持单章 ✅（无误触发） |
| ⚠️ 环境受限 | **B站 412 页面级风控**（2026-11-14）——IP 信誉被当天反复自动化访问烧坏后升级到验证码，冷却无效。已建 Playwright+SESSDATA 浏览器兜底（bilibili_extractor.py，页面能加载时是确定性方案），但 IP 信誉差时兜底也过不去；换网络（手机热点）或等数小时~一天自动恢复（2026-11-15 晨实测已恢复）。抖音不受影响 |
| ✅ 已闭环 | **导入自动建「新导入」会话撤回**（2026-11-15，主人判定设计漏洞污染问答侧栏）——ADR 0005 阶段3 撤回：worker 两处 summarize_import 调用移除、15 个存量自动会话清理、导航「后端正常」健康指示移除 + 个人中心右移 |

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
| `delivery/2026-11.md` | 2026-11 起：视觉/扫描件/编辑/CSV/SVG/附件/评测归档/v18 渲染 |

> 2026-11-01 起的交付（视觉/扫描件/编辑/CSV/SVG/附件/评测归档/v18 渲染）已补齐交付块，见 `delivery/2026-11.md`。
> **从下一次交付起，仍按 `HANDOFF.md` §4 的格式写块**，写进对应月份的归档文件，不再往速览下面追加。

---


