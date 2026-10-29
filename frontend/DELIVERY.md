
# frontend/DELIVERY.md

> 只由**前端线**写（HANDOFF §0）。其他角色的文件不在这里记录。

---

## 【接手确认单 · DSH-前端 · 线 L】

```
1. 当前状态我的理解
   - 已完成：后端导入链闭环（上传 → arq 排队 → G0/G1/G2 → note → chunk → embedding）、
             混合检索（RRF 双路）、/healthz；线 M 的 4 个查询接口联调时实测已可用
   - 前端基线：只有空白首页（HomeView）+ /healthz 封装 + ApiError 雏形
   - 还没做：三页界面、Pinia store、Vue Router 路由、全局样式
2. 我的任务：做导入 / 笔记库 / 检索三页能演示的界面，按 docs/api.md v1 写类型，
             顺手修掉 vite proxy 的 rewrite bug
3. 我打算创建/修改的文件：见下面「产物」清单（全部在 frontend/ 内）
4. 我知道不能碰的：
   backend/ · docs/ · eval/ · .env · AGENTS.md · HANDOFF.md · package.json · 任何 git 命令
5. 我拿不准的：见文末「需要确认的问题」（主要是 docs/api.md 与真实返回不一致的几处）
```

---

## 2026-10-30 02:50 · 线 Q-批8 · 设置页（模型配置热生效）· 执行者：ZCode（主人批准）

- `src/api/settings.ts`：getSettings / updateSettings（PUT；api_key 空串=保持不变）
- `src/views/SettingsView.vue`：三字段（网关地址/模型/API Key 密码框）+ 已设置末四位提示 + 保存即生效；nav 加「设置」；router 加 /settings
- `src/api/http.ts`：加 `put()` 方法

自测：`pnpm build` 1.08s 通过（vue-tsc 零错误）。

---

## 2026-10-30 02:30 · 线 Q-批7 · 问答页流式渲染（fetch 流读取 + 打字机）· 执行者：ZCode（主人批准）

- `src/api/qa.ts`：`streamChat(sessionId, question, handlers)`——fetch + ReadableStream 手工解析 SSE 帧（EventSource 不支持 POST）；流式读取在 api 层实现，组件不直接 fetch
- `src/views/QaView.vue`：发送走流式——meta 替换乐观消息并挂空气泡（显示「…」），delta 增量打字机，done 用最终消息（含出处/缓存命中率）替换；错误事件走 errorText
- `src/api/http.ts`：导出 BASE_URL（流式 fetch 复用同一基址）

自测：`pnpm build` 701ms 通过；后端流式 e2e 已验（126 delta 帧）。

---

## 2026-10-30 00:55 · 线 Q-批4 · 会话导出按钮 + 文案去模型名 · 执行者：ZCode

- 会话列表每项加「↓」导出按钮（`<a href download>`，走后端 export 端点，浏览器下载 .md；后端同时在 tmp/qa_exports/ 落一份给 agent 读）。
- 页面文案「DeepSeek 前缀缓存命中率」→「模型前缀缓存命中率」（所有支持缓存的模型通用措辞）。

自测：`pnpm build` 837ms 通过。

---

## 2026-10-30 00:36 · 线 Q-批3 · 主人实测反馈 5 点修复 · 执行者：ZCode

主人浏览器实测批2 后的 5 点反馈，全部修复：

1. **「内容好像没发出去」** → 乐观上屏：发送瞬间你的消息就进消息流（临时 id），响应回来换成真实 id；失败撤下并**还你输入内容**。
2. **「发你好也有出处」** → 后端根因修复（只显示答案真正引用的 [n]，见 backend/DELIVERY 线 Q-批3），前端随之不渲染空出处。
3. **「格式不优美、离左边太近」** → 根因：批2 重构时把全局 `.md` 样式类弄丢了（列表缩进/代码块/段距全失效）。已加回 `class="md answer-md"`，assistant 气泡 padding 12/16 → 14/18。
4. **「缓存命中率应在输入框下面、每次问答更新」** → 移出出处折叠，改为**输入框正下方的固定状态行**：显示最新一条答案的命中率（无数据的模型显示「—(当前模型未返回缓存数据)」，网关 agnes-3.0-flash 即此情况；切 deepseek-flash 出真实数字）。
5. **「发送按钮两字上下排」** → `.chat-input .btn` 加 `white-space: nowrap; flex-shrink: 0`。

### 自测
- `pnpm build`（vue-tsc + vite）→ **695ms 通过**
- 后端 50 passed（出处过滤新例 3 条 + 断言改差值，见 backend/DELIVERY 线 Q-批3）

### 需要确认的问题
- （无）

---

## 2026-10-30 00:09 · 线 Q-批2 · 问答页重构为聊天框（多轮 + 会话管理 + 缓存徽章）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 改 | `src/api/qa.ts` | `sendQuestion(sessionId\|null, q)` / `listSessions` / `getSession` / `deleteSession`，类型照新契约 |
| 改 | `src/views/QaView.vue` | **重构为聊天框**：左侧会话列表（重开/删除/新对话）；消息流（用户右/助手左，markdown 渲染，出处折叠 + 缓存命中率徽章，最新一条默认展开）；底部输入 Enter 发送；自动滚底 |
| 改 | `src/api/http.ts` | 加 `del()`（DELETE；204 无响应体返回 null，其余 2xx 照常解析） |

### 自测

- `pnpm build`（vue-tsc --noEmit + vite build）→ **837ms / 58 modules 通过**
- 后端两轮 e2e 已验证（见 backend/DELIVERY 线 Q-批2）；侧栏/重开/删除交互与 API 测试同口径
- 未起 dev server（§3.1）；`pnpm dev` 手动点一遍留给主人验收

### 已知限制

- 缓存徽章在当前网关不显示（后端 cache_hit_rate=None 时徽章隐藏；切 deepseek-flash 即显示）。
- 非流式：发送 → 「思考中…」→ 整条消息出现（SSE 是后续增量）。

### 需要确认的问题

- （无）

---

## 2026-10-29 23:07 · 线 Q-批1 · 问答页（提问 → 答案 + 出处）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `src/api/qa.ts` | `askQuestion(q, topK)` via `http.post('/qa')`，字段照后端 `QaResponse` |
| 新增 | `src/views/QaView.vue` | 照 SearchView：问题输入 → loading → 答案（markdown 渲染）→ 出处列表（**可点跳笔记详情**）；本地 ref 无 store（SearchView 同惯例） |
| 新增 | `src/utils/markdown.ts` | **从 NoteView 抽出的**手写 markdown 渲染器（无第三方依赖），NoteView 与 QaView 共用 |
| 改 | `src/views/NoteView.vue` | 内联渲染器换成 import `utils/markdown`（纯移动，行为不变） |
| 改 | `src/router/index.ts` | 加 `{ path: '/qa', name: 'qa' }`；注释「三页」→「四页」 |
| 改 | `src/App.vue` | nav 加「问答」链接 |

### 自测

- `pnpm build`（= `vue-tsc --noEmit && vite build`）→ **type-check 通过，built in 938ms**（58 modules）
- 后端 API 已用真实 DeepSeek 端到端验证（见 backend/DELIVERY 线 Q-批1）；点出处跳详情的 `router.push('/notes/{id}')` 与 SearchView 的 openNote 同一实现
- 未起 dev server（HANDOFF §3.1）；`pnpm dev` 手动点一遍留给主人验收

### 已知限制

- **非流式**：loading → 整段答案（与后端同口径；SSE 流式是后续）。
- 答案里的 `[n]` 是纯文本标记，不做成可点链接（出处列表已可点跳详情）。
- 无问答历史（单问单答；多轮会话是路线图阶段2）。

### 需要确认的问题

- （无）

---

## 2026-10-29 · 线 L · 三页界面 · 执行者：DSH-前端

### 产物

| 动作 | 路径 | 干什么 |
|---|---|---|
| 改 | `frontend/vite.config.ts` | **删掉 `rewrite`**（后端路由本身带 `/api`，剥前缀导致全 404）；补 `/healthz` 代理 |
| 改 | `frontend/src/api/http.ts` | 补 `postForm`（multipart，不手写 Content-Type）；从错误响应体取 `detail` 填进 `ApiError.message`；`describeApiError` 出人话 |
| 新增 | `frontend/src/api/sources.ts` | `POST /api/sources` · `GET /api/sources/{id}` + 类型（逐字照 docs/api.md §2） |
| 新增 | `frontend/src/api/notes.ts` | `GET /api/notes` · `GET /api/notes/{id}` + 类型（§3） |
| 新增 | `frontend/src/api/search.ts` | `POST /api/search` + 类型（§4） |
| 新增 | `frontend/src/stores/import.ts` | Pinia：串行上传队列 + 1s 轮询 + 状态机 + **定时器清理** |
| 新增 | `frontend/src/views/ImportView.vue` | 拖拽/点选、卡片、五态、三道闸 |
| 新增 | `frontend/src/views/LibraryView.vue` | 列表 + 标题搜索（300ms 防抖） |
| 新增 | `frontend/src/views/NoteView.vue` | 详情：markdown 渲染（自带最小渲染器）+ 父块折叠 |
| 新增 | `frontend/src/views/SearchView.vue` | 检索：rrf 4 位小数、两路名次徽章、300 字预览可展开 |
| 新增 | `frontend/src/styles.css` | 全局样式（手写，无 UI 库） |
| 改 | `frontend/src/router/index.ts` | `/import` · `/notes` · `/notes/:id` · `/search` · `/` 重定向 · 兜底 |
| 改 | `frontend/src/App.vue` | 顶部导航 + 健康状态黄条 + RouterView |
| 改 | `frontend/src/main.ts` | 引入 `styles.css` |

**没有动**：`package.json`（零新增依赖）、`.env`、`AGENTS.md`、`HANDOFF.md`、`backend/`、`docs/`、`eval/`。

### 修掉的 bug（vite proxy rewrite）

```diff
       '/api': {
         target: 'http://127.0.0.1:8000',
         changeOrigin: true,
-        rewrite: (path) => path.replace(/^\/api/, ''),
       },
```

删掉 rewrite 之后 `/api/healthz` 会打到后端的 `/api/healthz`（404），
所以 `http.ts` 里把 `/healthz` 当作「少数不带 /api 前缀的端点」特判成 `/healthz`，
并在 vite.config 里单独加了一条 `/healthz` 代理。**没有改 `api/health.ts`**（不在预批清单里）。

### 自测（真实输出）

```
$ cd frontend; pnpm type-check
$ vue-tsc --noEmit
（无输出，exit 0）

$ cd frontend; pnpm build
vite v7.3.7 building client environment for production...
✓ 53 modules transformed.
dist/index.html                   0.41 kB │ gzip:  0.30 kB
dist/assets/index-Bjq20q_t.css    9.34 kB │ gzip:  2.46 kB
dist/assets/index-D7lKMQuP.js   121.84 kB │ gzip: 47.19 kB
✓ built in 918ms
```

> 上面是**改完最后一行代码之后**复跑的输出（type-check exit 0 / build exit 0）。
> `frontend/dist/` 已被根 `.gitignore` 的 `**/dist/` 覆盖，不会误入库。

### 端到端联调（这次真跑了，不是纸面验证）

条件：`uv run uvicorn app.main:app --port 8000` + `pnpm dev`（5173），Playwright(chrome) 驱动。

| 场景 | 结果 |
|---|---|
| `/import` 空态 | 有拖拽区 + 空态文案 |
| `/import` 真上传（临时 .txt） | 卡片显示 `排队中` / `等待解析` / `已存在，未重复入库` / `刷新状态`（**没起 arq worker，job 停在 queued**，属预期） |
| `/import` 三道闸（浏览器层 mock payload） | G0 通过 / G1 通过 质量:high / G2 收下 质量:high + 每条 reasons；`no_text_expected` 是**灰**徽章不是红；`unsupported_format` 显示 `请转成 .docx`；`deduped` 显示「已存在，未重复入库」 |
| `/notes` | `共 11 条`，11 张卡（质量/父块数/来源/时间） |
| `/notes` 搜索「实训」 | 防抖后 `共 1 条` |
| `/notes/164` | 标题正确、父块 15 个、折叠可展开 |
| `/search`「内存泄漏怎么排查」 | 命中 5 条、`耗时 183 ms`、`rrf 0.0621`、徽章 `向量 #2` / `词法 #1` / 「两路都命中，最可信」 |
| `/search` 点第一条命中（mock 一条 hit） | URL 变 `/notes/164`、详情页标题正常、回到笔记库链接在 |
| 详情页 markdown 渲染（mock 一份真 markdown） | `h1 / strong / em / code / ul / blockquote / pre` 全部渲染并套上排版 |
| `/search` 空命中（mock `hits: []`） | 「库里没有相关内容」，红色报错块 **0** |
| `/notes` 404（mock） | 「后端接口还没就绪(404):接口还没实现」，页面标题照常渲染，不白屏 |
| `/notes/999` 404（mock） | 「后端接口还没就绪(404):笔记不存在:999」+ 「← 回笔记库」 |
| `/healthz` = error（mock） | 顶部黄条「后端未就绪 status=error · db=ok · redis=error」 |
| 后端真的关掉时（真链路） | 黄条「后端未就绪 后端没正确响应(500);先确认后端是否已启动」 |

浏览器控制台：`pageerror` **0** 条；`console` 只有 1 条浏览器默认的 `/favicon.ico` 404
（`index.html` 没有 favicon link，不是业务请求；第二次进页面就不再出现）。

**截图**（在 `%TEMP%\baleen-shots\`，没进仓库）：
`1-import-empty` · `1-import-uploading` · `2-library` · `2-library-search` ·
`3-note-detail` · `4-search-empty` · `4-search-hits` · `4-search-empty-result` ·
`5-search-empty-hits` · `6-notes-404` · `7-note-404` · `8-health-banner` · `9-import-gates` ·
`10-markdown-render`

> 自查时自己抓到并修掉的两个问题（都已复测）：
> ① 详情页 `<article>` 漏了全局 `.md` 类 → markdown 只出语义没有排版（**已修，截图 10 复验**）；
> ② `describeApiError` 对 5xx 回显 `请求失败(500:Internal Server Error)` 这种没用的话术 → 改成
> 「后端没正确响应(500);先确认后端是否已启动」（dev 期 500 基本都是后端没起，Vite 代理自己回的）。

### 清理（HANDOFF §3.1）

两个进程都用 `taskkill /T /F` 收掉了，自查：

```
端口 5173/8000 监听：(空)
uvicorn.exe：(空)
```

> 顺带说明：自查时看到两个 `python.exe ... eval/scripts/ablation.py`，**是评测线（线 N）在跑，
> 不是我的，我没动它们**。

### 已知限制

1. **`GET /api/notes/{id}` 的 `markdown` 实测全是 `null`**，所以详情页正文区现在显示
   「这条笔记没有 markdown 正文…下面的父块原文是它的真源」。前端已兼容，但**这不该是最终形态**（见下节）。
2. **检索没有相关性阈值**：随手输入「zzz这个词库里肯定没有zzz」照样返回 5 条近邻（向量路只按距离取 top_k）。
   所以「库里没有相关内容」在真实链路上**几乎不会触发**（我是在浏览器层 mock `hits: []` 验证的这条分支）。
   答辩演示时如果不希望出现「问了不相干的问题，结果还是 5 条」，需要后端加分数阈值。
3. markdown 渲染器是**手写最小集**（标题/段落/列表/引用/代码块/粗体斜体行内代码/分隔线），
   不支持表格、图片、脚注、HTML 内联。理由：不许新增依赖（AGENTS §3.2）。先转义再拼标签，不会 XSS。
4. **没做路由懒加载**：3 个视图全静态 import。理由：体积小、答辩现场少一次网络往返、`vue-tsc` 能一次查全部视图。
5. `src/views/HomeView.vue` 现在是**孤儿文件**（路由里已不用）。我没删（不在预批清单里），请协调方决定删还是留作占位。
6. 控制台那条 `/favicon.ico` 404 要根治得动 `frontend/index.html`（加一行 `link rel=icon`），也没在预批清单里，我没动。
7. 联调时往 dev 库写了**一条真实 source（#238，文件名「演示-闭包与内存泄漏.txt」，job 停在 queued，
   没生成 note）**，要清干净可以删掉那一行。

### 需要确认的问题（docs/api.md 与真实返回不一致）

联调实测（2026-10-29，线 M 接口已在跑），**契约 v1 有 3 处跟真实数据对不上**，建议协调方核实后改文档：

| # | 位置 | 契约 v1 写的 | 实测 |
|---|---|---|---|
| 1 | `GET /api/notes/{id}` 的 `markdown` | `string` | **`null`**（所有笔记都是） |
| 2 | `GET /api/notes/{id}` 的 `lang` | `string`（例 `"zh"`） | **`null`** |
| 3 | `parse_status` 的失败值 | `failed` | 解析层实际产出 **`error`**（HANDOFF §1 线 B 的统计也是 `error=0`） |
| 4 | `GET /api/sources/{id}` 的 `ext` | 例 `docx`（无点） | 实际 **`.txt` / `.md`（带点）** |

前端的处理：`src/api/notes.ts` / `src/api/sources.ts` 在**网络边界做归一化**（null→空串、认不出的
`parse_status`→`failed`、认不出的 `job.status`→`queued`），TS 类型仍按契约声明。
所以就算后端不改，页面也不会被打挂；但**建议把文档改成实测形状**，别让下一个 agent 再踩。

另外一个小体验问题：新建 source 在 worker 还没处理时，`parse_status` 就是 `error` 且 `message=null`。
前端靠 `job.status` 判断成「等待解析」（中性色）绕过了，但**后端初始值给 `error` 语义上不对**。

### 断点在哪 / 下一步

- 三页都能点、能演示；**所有状态机与文案都在**，缺的是「真实数据下的 markdown 正文」和「检索相关性阈值」。
- 想真看到 G0/G1/G2 全绿：起 arq worker 再传一次文件（我这次没起，避免拉 1.2 GB 模型占时间）。
- 建议下一批：① 后端把 `markdown` 写进去（或文档改成允许 null）；② 检索加分数阈值；③ favicon；
  ④ 问答生成页（下一批功能）；⑤ 决定 HomeView.vue 去留。
