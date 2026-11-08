# 后端交付归档 · 问答与评测：线 Q 批1-10 · 线 R 回填 · G2 真跑 · 切块档位重跑

> 从 `backend/DELIVERY.md` 拆出（2026-11-07），**只搬运、未改写**。
> 本文件 13 个交付块，按原文件顺序排列。

---

## 2026-10-30 02:14 · 线 Q-批6 · 阶段2.5 问题改写（追问检索脱靶根因修复）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `app/service/qa/rewriter.py` | `rewrite_question`（condense question）：LLM + 对话历史把追问改写成独立检索问题（「它有哪几种状态」→「Promise 有哪几种状态」）；**best-effort**——LLM 异常向上抛由 handler 兜底回退原问题（与 answer 的「向上抛不降级」口径不同，改写是锦上添花）；防注入同款；无历史不调 LLM |
| 改 | `app/api/qa.py` | chat 接入：历史两版（llm_content 重放版给 answer / content 可读版给改写器）→ 有历史先改写（失败回退）→ `retrieve(search_query)` → answer 仍用原问题+llm 重放历史；ChatResponse 加 `search_query`（实际检索词透出） |
| 测试 | `tests/service/qa/test_rewriter.py`、`tests/api/test_qa.py` | rewriter 6 例；API 重构为响应队列夹具，新增改写命中/失败回退/首轮不改写 3 例 |

### 自测
- `pytest tests/service/qa tests/api -q` → **58 passed**；ruff 全清；`pnpm build` 682ms
- **真实 e2e（glm-5.2，复现主人当时失败的场景）**：T1「什么是 Promise，怎么手写」→ T2「它有哪几种状态」→ 改写检索词 =「Promise 有哪几种状态」→ 正确答出三状态带引用（当时是「没找到」）；**T2 缓存命中率 46.3%**（llm_content 重放生效）；测试会话已删

### 已知限制
- 每轮追问多 1 次 LLM 调用（glm-5.2 约 2-6s；改写 prompt 的历史前缀逐轮相同 → 缓存摊薄成本）
- `search_query` 不落库：重开历史会话不回放检索词（要落库需加列，价值低）
- 改写质量依赖模型；极端改写错误由「回退原问题」兜底

### 需要确认的问题
- （无）

---

---

## 2026-10-30 02:50 · 线 Q-批8 · 模型配置 API（设置页后端，热生效）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `app/models/setting.py` | AppSettings 单行表（id=1；字段 NULL = 回退 .env 对应项） |
| 新增 | `alembic/versions/0005_app_settings.py` | 已 upgrade head 实跑 |
| 新增 | `app/repo/settings.py` | SettingsRepo.save（None=保持/空串=清空回退；api_key 仅非空时更新） |
| 新增 | `app/service/llm/resolve.py` | `resolve_llm_config(session)`：DB 优先 → 回退 .env，返回 (base_url, api_key, model) |
| 新增 | `app/api/settings.py` | GET（生效值 + api_key 只回「已设置+末4位」，**永不回显全文**）/ PUT（热生效） |
| 改 | `app/api/qa.py` | `get_llm_client` 改为读 DB 设置（每次请求现读 → 热生效）；测试 override 兼容 |
| 改 | `app/main.py` | 注册 settings router |

### 自测

- `pytest tests/api/test_settings.py -q` → **4 passed**（回退/往返/空 key 保持/key 不回显）；全量 63 passed；ruff 全清；alembic 0004→0005 实跑

### 已知边界

- **worker（导入 G2）暂仍走 .env**——设置页当前只影响问答/改写链路；worker 侧统一排下一步

---

---

## 2026-10-30 02:30 · 线 Q-批7 · SSE 流式问答（打字机效果）· 执行者：ZCode（主人批准）

### 产物

- `app/service/llm/client.py`：Transport 加 `request_stream`（Urllib 流式实现）；`LLMClient.chat_stream` 同步生成器——SSE 逐行解析，reasoning/content/usage 三类事件分流，缓存双格式归一复用 `_cache_fields`；流式不重试（中途无法续传），打开失败即抛
- `app/api/qa.py`：`POST /api/qa/chat/stream`——改写与检索在流开始前完成；轮次开始即 commit 用户消息，流结束 commit 助手消息（含出处/缓存）；事件流 meta → delta* → done，LLMError 以 error 事件透出
- 无状态 POST /api/qa/chat 保留（非流式兜底）

### 自测

- `pytest tests/service/qa tests/api/test_qa.py -q` → **28 passed**（新增 SSE 端点用例：meta/delta/done 帧序、OpenAI 风格 cached_tokens 归一 0.6、流后落库断言）；ruff 全清
- **真实流式 e2e（glm-5.2）**：**126 个 delta 帧**、拼接 2096 字、done 缓存命中率 **82.9%** + 2 条出处、落库 [user, assistant]；测试会话已删

### 已知限制

- reasoning（思考过程）增量暂不转发——思考期间前端显示「思考中…」，正文开始后打字机
- 客户端中途断开：用户消息已落库、无助手消息（语义可接受）

---

---

## 2026-10-30 01:49 · 线 Q-批5 · 缓存命中率真落地（llm_content 重放 + 切 glm-5.2）· 执行者：ZCode（主人指令）

主人指令「解决缓存不显示」。三步走全通：

| 动作 | 路径 | 说明 |
|---|---|---|
| 改 | `.env`（**主人明确指令代改，破例记录在案**） | `LLM_MODEL` agnes-3.0-flash → **glm-5.2**（只动这一行，key 未触碰未打印） |
| 新增 | `alembic/versions/0004_qa_message_llm_content.py` | qa_messages 加 `llm_content`（已 upgrade head 实跑） |
| 改 | `app/models/qa.py`、`app/repo/qa.py` | user 消息双份存储：`content`（原始问题，给人看）+ `llm_content`（含 `<context>` 的原文，给 LLM 重放） |
| 改 | `app/api/qa.py` | 先组装消息取 `<context>` 原文存 llm_content；历史按 **llm_content 原样重放**（前缀逐轮对齐） |
| 改 | `app/service/qa/answerer.py` | 历史重放预算改安全网（200 条/24000 tok）——**裁剪会破坏前缀对齐**，轻易别调小 |

### 自测
- pytest 21 passed（qa 相关）；ruff 全清；alembic 0003→0004 实跑
- **真实两轮 e2e（glm-5.2）**：T1「什么是 Promise，怎么手写」缓存命中率 **0.0**（冷启动）→ T2「手写的时候要注意什么」**48.4%**（前缀对齐命中，计费约一折）；答案质量正常带引用；测试会话已删
- 探针：glm-5.2 双发同前缀 4821 tok → call1 cached_tokens=0 → call2/3 **4608（95.6%）**

### 原理（为什么之前一直是 —）
前缀缓存按「请求开头完全相同的部分」命中。旧实现用户消息存原始问题、LLM 却看 context 包装版 → 下轮前缀在 system 之后立刻分叉，稳定前缀只剩 ~450 tok（低于上游门槛）。现在按 llm_content 原样重放，会话内第 N 轮的前缀 = 第 N-1 轮完整 prompt → 命中率随轮数上升。

### 已知限制
- 命中率取决于上游账号池路由（跨账号不共享缓存）；未命中轮 0.0/—。
- 提醒主人：**dev server 要重启**（.env 与代码都变了）；worker 仍挂本会话下。

---

---

## 2026-10-30 00:55 · 线 Q-批4 · 会话导出 + 两个运行诊断 · 执行者：ZCode

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 改 | `app/api/qa.py` | 新增 `GET /api/qa/sessions/{id}/export`：生成 Markdown（元信息 + 逐条消息含 model/token/缓存命中率 + 出处）→ **写一份到 `tmp/qa_exports/session-{id}.md`**（gitignored，agent 可直接读）→ 同时以附件返回浏览器下载 |

### 两个诊断（重要，主人问的）

1. **缓存命中率显示「—」的根因（已订正）**：初判「网关剥字段」**是错的**——主人指正：网关透明转发 usage，只出现 3 字段是因为**那次请求未命中缓存**（上游未命中时本就不带该字段）；call_logs 证实 262/357 次 deepseek-flash 拿到非零 cached_tokens。已修：**client 解析兼容两种格式**（DeepSeek `prompt_cache_hit_tokens` + OpenAI 官方 `prompt_tokens_details.cached_tokens`，miss = prompt−hit）。⚠️ 实测仍待观察：真实两轮（4000+ tok prompt）仍未上报字段——命中与否取决于上游账号池路由/缓存门槛，不再是解析问题，命中即显示。
2. **「排队中」= arq worker 进程没启动**：Redis 里积压 3 个 `arq:job:import:*` + `arq:queue`，进程列表无 arq。启动命令：`cd backend → uv run arq app.worker.WorkerSettings`（max_jobs=1 串行；每个文件 = G0+G1+G2 一次 LLM 调用+bge 向量化）。⚠️ 网关限流冷却期 G2 会失败，任务可能 failed，等冷却过重试。**已实跑验证**：worker 启动后积压 3 任务全部消费（含等了 31 分钟的 source 333「03-命令速查.md」，12s 处理完 → ok），新上传即到即处理，笔记 11→13 篇。
3. **新发现的真实体验问题（记入路线图，未修）**：追问型 query（「它有哪几种状态」）指代重、检索脱靶，模型按规则拒答——需要「**问题改写**」（用历史把指代展开后再检索，RAG 标准做法），排阶段 2.5。

### 自测
- `pytest tests/api/test_qa.py -q` → 6 passed（新增导出用例：附件 + tmp 落盘 + 404）；ruff 全清

---

---

## 2026-10-30 00:36 · 线 Q-批3 · 主人实测反馈修复（出处过滤）· 执行者：ZCode

主人浏览器实测批2 后反馈 5 点（非流式/单轮/聊天框 UI 属于既有路线图，本块只记代码修复）：

- **「发你好也挂 5 条出处」→ 根因修复**：`answerer.answer()` 现在**只保留答案里真正引用的 [n]**（`re.findall(r"\[(\d{1,2})\]")`），问候/闲聊没有 [n] → citations 为空；幻觉编号（超命中数）剔除。已知取舍：代码块里的 `arr[1]` 会被当成引用（最坏多显示一条检索命中，可接受）。
- 测试 +3：只引用部分/无标记/越界编号。

### 自测
- `pytest tests/service/qa tests/api -q` → **50 passed**；ruff 全清。
- ⚠️ 测试断言改为「前后差值」：主人浏览器测试在 dev 库留下了真实会话（qa_sessions id=9「你好」），**测试不再假设空库**——后续任何人写 API 测试都注意这点。

---

---

## 2026-10-30 00:09 · 线 Q-批2 · 聊天式多轮问答（会话落库 + 缓存命中率）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `app/models/qa.py` | QaSession / QaMessage（TEXT+具名 CHECK 家法；citations JSONB 存检索出处；**cache_hit/miss_tokens** 记前缀缓存原始计数） |
| 新增 | `alembic/versions/0003_qa_chat.py` | 0002→0003；autogenerate 后按家法整理；**已 `upgrade head` 实跑**，information_schema 确认两表建出 |
| 改 | `app/models/__init__.py`、`app/repo/repos.py` | 注册新模型 + QaRepo |
| 新增 | `app/repo/qa.py` | create/list/get/delete_session + list_messages + add_message；**只 flush**，调用方 commit（upload.py 同模式） |
| 改 | `app/service/llm/client.py` | ChatResult 加 `prompt_cache_hit_tokens / prompt_cache_miss_tokens`（从 usage 解析；provider 无此字段 → None，向后兼容） |
| 改 | `app/service/qa/answerer.py` | `answer()` 加 **history**（system → 最近 8 条/2000 tok 封顶裁最旧 → 当前 user 带 `<context>`）；短路条件改为「命中与历史**都**空」（追问靠历史不短路）；系统提示适配多轮（指代消解 + [n] 只对当前 context 有效） |
| 改 | `app/api/qa.py` | **替换**无状态 `POST /api/qa`：`POST /api/qa/chat`（session_id 空自动建会话，title=首问前 30 字；一条提问原子落库两条消息）+ `GET /api/qa/sessions` + `GET /api/qa/sessions/{id}` + `DELETE /api/qa/sessions/{id}`（FK CASCADE） |

### 自测

- `uv run pytest tests/service/qa tests/api -q` → **47 passed**（answerer 12 例含历史裁剪/缓存解析新例；API 6 例走 env 事务隔离夹具，真实落库后回滚不污染库）
- **真实两轮 e2e（TestClient + 网关，测试会话已删）**：T1「闭包是什么」10.2s 带引用；T2「那它的三个形成条件是什么」**用上历史**正确答三条件带 [1]，17.8s；重开会话 4 条消息；侧栏 total=1；DELETE 204
- 临时资源自查（§3.1）：全程 TestClient，未起 uvicorn

### 已知限制

- **cache_hit_rate 当前显示 None**：`agnes-3.0-flash`（主人已把 .env 从 DeepSeek 切回内网网关）的 usage 不带 cache 字段；显示管线端到端已接好，**切回 deepseek-flash 即有真实数字**。
- 多轮历史只带最近 8 条 / 2000 token（超了从最旧丢）——远期上下文不进 LLM。
- 非流式（「思考中…」→ 整条消息）；会话列表无分页（上限 50）。

### 需要确认的问题

- （无）

---

---

## 2026-10-29 23:07 · 线 Q-批1 · 问答 API（检索 → LLM 答 + 出处）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `app/service/qa/__init__.py`、`app/service/qa/answerer.py` | `build_qa_messages`（命中片段编号塞 `<context>`、按 token 预算截断、防注入声明）+ `answer`（**命中为空短路不调 LLM**；同步，调用方 `asyncio.to_thread`） |
| 新增 | `app/api/qa.py` | `POST /api/qa`：`RetrieveDep`（复用线 M 的延迟导入模式，启动不拉 torch）→ `asyncio.to_thread(answer)` → `{question, answer, citations[n/note_id/note_title/heading_path/snippet], took_ms, cost_tokens, model}` |
| 改 | `app/main.py` | 注册 qa router（+2 行） |
| 新增 | `tests/service/qa/test_answerer.py`、`tests/api/test_qa.py` | FakeTransport **不联网**；answerer 6 例 + API 3 例 |

**没碰的**：冻结区（`chunk/`/`embed/`/`parse/`/`imports/`/`repo/`）、`g2.py`（阶段0 验活结论：DeepSeek 下无需改动）。

### 自测

- `uv run pytest tests/service/qa tests/api/test_qa.py -q` → **9 passed**
- `uv run pytest tests/api -q` → **34 passed**（qa router 加入后无回归）
- `uv run ruff check`（新文件 + main.py）→ 全清
- **真实端到端（TestClient，不起服务器）**：问「闭包是什么」→ 200，答案带 `[2]`/`[3]` 内联引用 + 代码块 + 三条形成条件，5 条出处全指向笔记 #156 的具体小节，took 4.6s / 2169 tokens / deepseek-flash
- 临时资源自查（HANDOFF §3.1）：全程 TestClient，未起 uvicorn，端口/进程清零

### 顺带：阶段0 —— G2 在 DeepSeek 上验活

主人把 `.env` 换成 DeepSeek 云（`https://api.deepseek.com/v1` / `deepseek-flash`）后：
`scripts/g2_smoke.py --samples ../eval/samples/_inbox --live --limit 1` →
**verdict=accept / quality=normal / action=index / score=4，cost 3793 tok，3.4s，降级 0**；`probe()` 报 `json_mode=True`。
**结论：reasoning 模型下 G2 无需改动**（`max_tokens=800` 够出 JSON）。
⚠️ `deepseek-flash` 是推理模型，reasoning 先吃 completion token；G2 的 800 够用，**问答侧已把 `max_tokens` 提到 3000**。

### 已知限制

- **非流式**：整个答案一次返回（前后端都无 SSE 基建，流式是后续优化）。
- **无状态**：不记问答历史（多轮会话是路线图阶段2，需新表 + Alembic migration）。
- LLM 异常**向上抛**（与 G2 同口径）：endpoint 挂了 → HTTP 500，前端 `describeApiError` 翻译成可读文案。
- 答案里的 `[n]` 是 LLM 写的，citations 是检索命中全量（不做 `[n]`↔citation 严格对齐校验）。

### 需要确认的问题

- （无）

---

---

## 2026-10-30 03:10 · 线 Q-批9 · 阶段3 导入后主动总结 · 执行者：ZCode（主人批准，D1=A）

### 产物
- `app/service/qa/summarizer.py`：`summarize_import(note_id, filename)`——读笔记父块 → LLM 摘要（要点+3 建议问题）→ 自动建「新导入:xxx」会话（摘要=首条 assistant 消息）；含 `organize_session_note`（阶段4 整理器）
- `app/worker/worker.py`：import_file 成功后挂摘要步骤（`note_id 存在 且 非 dedupe 且 非 no_llm` 才触发；**失败仅 warning，不影响导入**——ADR 0005 §2 验收标准）

### 自测
- 单测 2 例（monkeypatch LibraryRepo 读取 + env 事务工厂；note 缺失返回 None）
- **真实 e2e（glm-5.2）**：对笔记 #297（03-命令速查.md）触发 → 会话 #179「新导入:03-命令速查.md」自动创建，摘要准确覆盖 uv run 规范/测试流程/Git 规范（测试会话已删）

### 已知限制
- dedupe 命中的旧笔记不补摘要（原导入早于本功能）
- no_llm 导入不生成摘要（口径一致：无 LLM 全程无 LLM）

---

---

## 2026-10-30 03:25 · 线 Q-批10 · 阶段4 会话产出笔记 + 工具链 Tavily 兜底 · 执行者：ZCode（主人批准，D2=B / D4=Tavily）

### 产物
- `app/api/qa.py`：`POST /api/qa/sessions/{id}/save-note`——LLM 把会话整理成结构化 Markdown → 落 `notes_out/` → **no_llm 入队**（G0/G1/切块/向量化照常，跳过 G2——D2=B 直接入库）；CitationOut 加 url/note_id 可空（联网出处）
- `app/service/qa/websearch.py`：Tavily 搜索（免费档 1000 次/月）
- `app/api/qa.py` chat/chat-stream：**库内未命中 + 配了 Tavily → 自动搜网**，回答带 [W n] 来源标注（citations.url 外链）；未配 key 或搜索失败 → 维持「没找到」口径
- `app/api/settings.py`：tavily_api_key 只写不读（tavily_set/tavily_tail）
- 前端：存为笔记按钮（chat 工具栏）、citations 渲染内链/外链双形态、设置页 Tavily 字段

### 自测
- `pytest tests/service/qa tests/api -q` → **68 passed**（新增 web 兜底/save-note/导出等 7 例）；ruff 全清；build 808ms
- **真实 e2e（glm-5.2）**：阶段4 对会话 #179 save-note → `notes_out/2026-10-30-新导入-03-命令速查.md.md`（1114 字，结构化准确）+ no_llm 入队成功

### 已知限制
- 联网兜底仅库内零命中触发；命中但内容不相关时不搜（需相关性判断，后续可做）
- 代码块 `arr[1]` 会被出处过滤误认为引用（可接受）
- save-note 的入库走队列异步完成,前端不跟踪其进度

---

---

## 2026-10-31 · 存量笔记标题回填（线 R）· 执行者：ZCode（主人批准；模型 glm-5.2 试 → deepseek-flash 收尾）

### 为什么做
标题整理（§9 / commit e913726）只挂在导入管线里，存量 15 篇没赶上。落地当时明确排除的「需单独回填脚本」即本节。

### 产物
- `app/service/rewrite/backfill.py`：`backfill_notes` / `list_candidates` —— 对存量 `notes.doc` 跑与导入**同款** rewrite（plan_outline → number_headings → apply_outline），单篇一事务：换 doc + 删光 chunks 重建 + 重嵌。幂等主闸 `sources.parse_meta.headings_rewritten`、省钱副闸 `needs_rewrite`；LLM 空计划不打标记（可重试 no-op）；单篇失败回滚不中断整批；`note.title` 与 `sources.doc` 均不动（保持「sources=原始解析」不变量）
- `app/repo/chunks.py`：补 `delete_by_note`（全仓第一个删 chunks 的方法；embeddings/子块靠 FK CASCADE 顺带清）
- `EMBEDDING_MODEL` 常量挪到 `app/service/embed/__init__.py`（pipeline / retriever / 检索测试改引用；顺带解除 retriever→pipeline 的重依赖）
- `scripts/backfill_outline.py`：薄壳（g2_smoke 骨架）—— 默认 dry-run 零 LLM 零写库；`--live` 每篇一事务；`--note-id/--limit/--resume/--delay`；`--llm-base-url/--llm-api-key/--llm-model` 临时覆盖（换模型不碰 DB/.env）；结果追加 `backend/tmp/backfill_outline_results.jsonl`（resume 只认 rewritten）
- 测试 7 例（`tests/service/rewrite/test_backfill.py` + conftest）：真 PG 外层事务回滚 + FakeTransport + 假 embed；覆盖整理重建/两类跳过/空计划/LLM 失败回滚/候选清单/级联删除

### 自测 / 验收
- pytest 全量 **239 passed + 1 failed** —— 挂的是 `test_qa` 存笔记拆分（上一会话未提交 WIP 自带，与本次无关）；ruff 全清
- **真实回填 15 篇收口**：11 篇 rewritten（433/487/156/157 用 glm-5.2 成在**小文档**；158/159/160/161/164/296/297 换 deepseek-flash，含 107 块大文档 +39 标题），4 篇该跳（10/27/70 <400 字；162 编号率 86% 结构已好）
- 幂等闭环：重跑 dry-run「待整理 0」；11/15 打标记；15 篇 chunks/向量行数齐全；检索冒烟 2 query top 命中均带新编号路径（「四、手写防抖和节流 > 带 leading/trailing 的防抖」）

### 已知限制 / 坑
- **glm-5.2（上游推理模型）小文档（~20 块内）能出，大文档 502/429/空 content**；回填期间上游还整波 502。标题整理/回填一律 deepseek-flash 稳
- LLM 按 prompt 规则 2 对既有标题在**原位**插编号版 → chunker 把空 section 丢掉、路径留原语义标题（管线同款既有语义，非回填引入）；编号进检索路径靠**新增**章节标题（158 实证「二、深拷贝」等前缀已进路径）
- 一次偶发 `MissingGreenlet`（run1 长等待后连接态；run2 全量未复现，数据无损，未深究——若复现再查 pool_pre_ping 在非 greenlet 上下文的 checkout）

---

---

## 2026-10-31 · G2 真跑评测（十节报告落地）· 执行者：ZCode（主人批准：完整报告路线）

### 为什么做
G2 自线 J 政策修正后从未全量真跑（唯一基线：修正前 live 误拒率 **53.3% (8/15)**）；TASK.md 定义的正式交付是
metrics.py 十节 report.md，此前一直是 fake predictor 占位。本节把「评测」变成真的。

### 产物
- `scripts/g2_eval.py`（新）：**产品真链路预测器** —— 对每个样本 `parse_file`（真 G0）→ 非 ok 确定性 reject/drop
  （产品本就不进闸）→ 真 G1（启发式）→ 过 → 真 G2（LLM）。preds.csv 10 列与 runner.py COLUMNS 逐列一致
  （rel_path 相对 samples 根），`raw_json` 记判定阶段/reasons/降级；G1 拒收不给 1-5 分（0-1 概率不同量纲，留空）。
  单文件失败写 error 列继续；`--resume`/`--limit`/`--delay` 齐全；LLM 三件套走环境变量覆盖（零文件改动）。
- `eval/preds_live.csv`（35 行）+ `eval/report.md`（**test split 主报告，从此是真数据**）+
  `eval/report_all.md`（全量口径）+ `eval/report_g2_ok.md`（**ok 子集口径**，分母 15，与 G1 的 0/15 同分母）。
- LLM：deepseek-flash 官方（probe json_mode=True）；总花费 **65,567 token**（G2 均值 ~3.6k/条，1.8–7.1s）。

### 自测（原始数字）
- 阶段分布：**G0 拦 14 + G1 拦 3 + G2 判 18**（16 accept/index、1 reject/topic_dialog、1 reject/drop），零 error 零降级。
- **误拒率（ok 口径，分母 15）= 0/15 = 0.0%** —— 达标线「≤1/15 且较 53.3% 实质下降」以最优值通过；非法组合 **0**。
- test split 2/10、all 3/18 的「误拒」**全部是 G0 解析不了**的 human-accept 文件（霸道总裁.doc + 两张手写 jpg）——
  产品限制非 G2 判错，报告全错清单可查。
- 唯一判定错误：1 条**误收**（项目实施记录（一）.md：human reject/topic_dialog → pred accept/index）——
  「误拒代价≫误收」哲学下可接受，进 mining list。
- 跑前自检：g2_smoke dry-run 确认 charter 1148 token（<1200 警戒线）、21 ok 文件全部按设计压进 4000 预算。

### 已知限制
- 误丢率 all 口径 1/2（human topic_dialog 全库仅 2 条，小样本无统计力）。
- ok 口径报告依赖 tmp/labels_ok.csv（由 preds_live.csv 的 stage 字段过滤生成，复现命令见 eval/DELIVERY）。
- 达标线为本次自定义（文档原无数字门槛），推导已写入本节；G2 prompt 未做任何改动。

---

---

## 2026-10-31 · 切块档位重跑（ADR §8 校准，15 篇语料）· 执行者：ZCode（主人批准）

### 为什么做
§8 要求对 TARGET_CHARS 消融校准，上次跑在 10-29 的 **11 篇语料 + q04 复核前标签**上；且线 R 回填改了
11 篇旧笔记的标题结构、语料新增 4 篇干扰项——旧结论必须在新语料上重验。

### 产物与结果
- `ablation.py` **一行未改**；`check_expected_paths()` 失配 **0**（回填编号标题被 chunker 丢空 section、
  期望路径全存活——queries.csv 一字未改，回填与导入同构的实证）。
- 三档 × 双口径（宽松 n=18 / 严格 n=16，bge 确定性、零随机、纯内存只读库）：
  R@5 = 94.4% / **94.4%** / 88.9%（宽松），93.8% / **93.8%** / 87.5%（严格）；
  300 档 **R@10 唯一 100%、MRR 双口径最高**（0.631/0.648）。
- 判决：**维持 300 档（= 生产现状）**——三项指标无一劣于两翼；300 vs 400 差 1 条 query（q13 掉出 top-5）；
  「400 是上限」复现（400 档 p90=444 字符、in_range 跌到 44.4%）。
- 报告：`eval/ablation.md` §12 + §1 更新行（论文引用以 §12 的 15 篇口径为准，§11 的 100% 是 11 篇历史值）。

### 已知限制 / 执行偏离
- §8 字面的 600 档未跑：bge 512 窗口下必静默截断，harness 刻意封顶 400（偏离已在 §12.4 存档）。
- 2 个原子代码块 >512 token（517/700）embedding 侧被截——既有取舍非回归（§1 结论 2 复现）。
- 300 档大节内 in_range 61.5%（<70% 目标、>60% 警戒线；4 篇小节型新笔记拉低）。
- 绝对数字与 §11 的 100% 不可直比（语料 11→15 + 回填改结构）；组内可比成立。
