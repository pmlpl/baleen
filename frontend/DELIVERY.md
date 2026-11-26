
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

## 2026-10-30 02:14 · 线 Q-批6 · 检索词显示（阶段2.5 配套）· 执行者：ZCode（主人批准）

- `src/api/qa.ts`：ChatResponse 加 `search_query`（改写后的实际检索词）
- `src/views/QaView.vue`：助手消息内出处折叠上方显示「检索词：xxx」（muted 小字；本地暂存自当轮响应，重开历史会话不回放）

自测：`pnpm build` 682ms 通过；后端改写 e2e 已验（见 backend/DELIVERY 线 Q-批6）。

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

## 2026-11-14 · 个人中心页 /profile · 执行者：ZCode

配套 GET /api/profile(v26,见 backend/delivery/2026-11.md 同日块)。主人 2026-11-12 提出。

### 改动
- **新增 `src/api/profile.ts`**:getProfile() 封装,类型 Profile(user/stats/charter/settings 四块)
- **新增 `src/views/ProfileView.vue`**:只读展示页——头像+用户名+账号 ID+注册时间、使用统计四格(笔记/导入来源/问答会话/问答消息)、章程预览卡(300 字截断,「…」结尾提示看完整)、配置掩码卡(网关/模型/API Key/Tavily,密钥只显末四位);两个按钮分别跳章程页/设置页,**不复制编辑逻辑**
- `router/index.ts` 加 `/profile` 路由(静态 import,与现有 5 视图同款);`App.vue` 导航加「个人中心」入口(仅 onboarding 完成后显示,与其它 nav-link 同条件)

### 自测
- `pnpm type-check`(vue-tsc) 通过
- 浏览器实测(5174 dev + 真后端):渲染/数据/高亮/截断提示全对,截图确认;测试用 token 已清
- 明确不做:改密码/邮箱/第三方登录入口(STATUS §7 划定)

### 已知限制
- 统计实时拉取:onActivated 初次挂载与每次重新进入都触发 load(),keep-alive 下切回个人中心统计也是新的,无缓存陈旧问题

---

## 2026-11-24 · C2 拆分批 · NotePicker + SlashMenu 从 QaView 拆出 · 执行者：前端代理

裁决来源:`docs/review-2026-11-23.md` §7——C2 只拆 NotePicker/SlashMenu 低风险子集,会话侧栏/SSE/附件/草稿明确不动,**行为零变化是验收标准**。

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `src/components/SlashMenu.vue` | 斜杠命令菜单,受控展示组件(泛型 C 保住调用方完整命令类型):props `open/commands/active`,emit `select/hover`;内部只有菜单 el ref + 亮项滚进可视区的 watch(从 QaView 搬来);`.slash-menu/.slash-item` 系样式原样搬入 |
| 新增 | `src/components/NotePicker.vue` | 引用笔记选择器弹层,受控展示组件(多根 fragment:全屏遮罩 + 菜单):props `open/notes/loading/query`,emit `close/select`;候选笔记/筛选词由父页传入,组件零 API 调用、零 store 访问 |
| 改 | `src/views/QaView.vue` | template 的斜杠菜单/选择器两块换成 `<SlashMenu/>`/`<NotePicker/>`;script 删掉搬走的 `slashMenuEl` + 滚动跟随 watch;新增 `pickNote()`(列表点击与 Enter 选第一篇合一,顺序=toggle→关选择器→清输入,与拆分前一致);搬走的样式从 QaView 删除。1990→1890 行(+2 组件共 275 行) |

**边界(什么留在 QaView、为什么)**:
- 输入框是共享的(命令词/笔记筛选/草稿/历史回溯都走它):键盘路由(picker→slash→Backspace 删 chip→历史)与开关状态(slashOpen/slashActive/slashMatches/notePicker/pickerNotes/pickerLoading)留父页,组件只收 props 发事件
- 笔记检索(listNotes)是 API 调用,组件内零 API 约束 → `loadPickerNotes`/300ms 防抖留父页
- 草稿逻辑(draftBeforePicker/loadDraft/草稿 watch)任务书明确不动,原样保留
- 引用 chips 的显示留在输入壳:与附件/图片 chips 同壳交织,Backspace 逆序删是跨三类 chip 的父级逻辑,拆出去必然碰附件关注点(明确不许)
- 菜单与选择器共用 `.slash-menu/.slash-item` 系样式 → 两组件各带一份 scoped 副本(注释互指),换共享需建全局样式文件,不在本批授权内

### 自测

- `pnpm type-check`(vue-tsc)→ **0 错误**;`pnpm build` → ✓ built in 32.52s
- Playwright 探针 `backend/tmp/probe_c2_split.py`(真后端 127.0.0.1:8000 + 真库,/healthz 预检,admin UI 登录):
  - functional **27 项断言两阶段(before=HEAD / after=拆分后)全绿**:输入 / 弹出 4 条命令、↓/↑ 键盘轮转亮项、过滤 /检索、Enter 选命令打开选择器+遮罩、列出真库笔记(实得 8 条)、点选回填 chip、双 chip Backspace 逆序删(后选的先删)、Esc 关闭还原草稿、点遮罩关闭、空态文案随筛选词切换、/新对话 命令生效清 chips、全程 0 pageerror
  - capture 5 状态(初始/斜杠菜单/过滤/选择器开/双 chip)前后对比 **10/10 逐字节相同**(DOM 规范化剥 data-v-*/注释/空白后逐字节同;截图 PNG 直接逐字节同)→ 视觉零变化
- 探针纯只读(不发消息不建会话,无测试数据要清理);前后端服务用完 `taskkill /T /F` 收净,8000/5173 端口与 uvicorn/python(baleen)/vite 进程自查为空

### 已知限制

1. SlashMenu 根元素会继承父作用域 `data-v-*` 属性(抽组件的标准 scoped CSS 行为,属性个数 +1);截图逐字节相同证明无视觉/交互影响,DOM 对比时已规范化剥除
2. `.slash-*` 系样式在两个组件里各有一份副本(约 40 行重复),两组件因此自包含;要消除需建共享样式文件,另批再议
3. **拆分中发现一个既有草稿竞态(非本批引入,未动)**:输入首个 `/` 字符时,v-model 的赋值监听器先于 `@input` 的 `updateSlash` 执行,草稿 watch 的 flush 窗口落在两者之间、守卫(slashOpen 仍 false)放行,`/`(整串命令词同理)被写进 localStorage 草稿;于是 `/新对话` 执行后 `loadDraft()` 把它还原进输入框(Playwright fill 与逐字键入均可复现,两阶段行为一致;物理键盘未测)。根因在 QaView 的草稿逻辑(本批明确不碰),建议另开一批修
4. 浏览器控制台仍有既有的 jsdelivr 字体 CDN 加载失败(环境无外网),与本批无关

### 需要确认的问题

- 已知限制第 3 条的草稿竞态要不要修(动 updateSlash/草稿 watch,建议单开一批)?

---

## 2026-11-24 · 草稿竞态修复批(DRAFIX)· 斜杠命令词不再写入会话草稿 · 执行者：前端代理

主人拍板修 C2 批发现的既有草稿竞态(上一块「已知限制」第 3 条,就此闭环)。

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 改 | `src/views/QaView.vue` | 草稿 watch 守卫加**值判据**:`if (notePicker.value \|\| slashOpen.value \|\| input.value.startsWith('/')) return`,并补注释写明根因与时序 |

**根因与修法**:slashOpen 只在 `@input` 的 `updateSlash` 里置位,而 v-model 的赋值监听先于 updateSlash 执行,watch 的 flush 可能落在两者之间的窗口里——窗口内 slashOpen 仍是 false,守卫放行,命令词被写进 localStorage 草稿;/新对话 一执行 `loadDraft()` 又把命令词还原进输入框。修法不加任何时序假设:以 `/` 开头的输入就是命令词(updateSlash 对每个输入事件都会重算 `slashOpen = startsWith('/')`),一律不存——flush 落在窗口内由值判据拦、落在窗口后由标志判据拦,**物理键盘按 flush 语义推理天然覆盖**(不是只堵 Playwright 复现路径)。

### 自测(先红后绿,证明探针灵敏度)

- Playwright 探针 `backend/tmp/probe_draft_race.py`(真后端 8000 + 真库,admin 登录;纯只读,不发送不建会话):
  - **修复前(红)**:17/26——9 个失败全是污染断言:fill 整词(草稿实得 `'/新对话'`)、逐字键入(草稿实得 `'/'`)、`/usr/local 路径正文`、`/检索` 四条污染路径全中
  - **修复后(绿)**:**26/26**——T1 fill 整词:预置草稿 `我的原草稿T1` 未被污染,`/新对话` Enter 后输入框**还原原草稿**(不再是命令词);T2 逐字键入:全程草稿不含 `/`,命令后输入框空;T3 正常文本照常存草稿 + 整页刷新后 loadDraft 还原进输入框(既有语义不变);T4 `/usr/local 路径正文` 菜单照常弹出、Esc 后输入仍在、之后正常文本照常存草稿(④ 不误伤);T5 选择器筛词/命令词不进草稿;0 pageerror
- **C2 回归**(同后端,`probe_c2_split.py`):functional **27/27** + 5 状态 DOM/截图对比 **10/10 逐字节相同**(本修复不改变任何已捕获的 DOM/视觉状态)
- `pnpm type-check` 0 错误;`pnpm build` ✓ 34.52s;`python scripts/doc_lint.py` 0 ERROR
- 服务进程用完 `taskkill /T /F` 收净,8000/5173 与 uvicorn/python(baleen)/vite 进程复查为空

### 已知限制

- 历史 localStorage 里已被污染的草稿(如有)不主动清洗:loadDraft 会原样还原,直到用户下次输入正常文本才被覆盖。正常 flush 下 `/` 开头的草稿本来就存不进去,污染值只可能来自本竞态,存量极少;要自愈可在 loadDraft 加一行 `/` 开头归空,未加(保持最小改动)
- executeSlash「先清输入再关菜单」的既有顺序纪律未动(③):空串非 `/` 开头,值判据不影响它,窗口内仍由标志判据兜住

### 需要确认的问题

- （无）

## 2026-11-25 · 问答页「历史对话点不开」修复 · 执行者：ZCode

**为什么做**：主人报换 Chrome 后侧栏历史会话点不开。排查链逐层排除（DB 44 会话消息全在 / 后端 API 列表与打开全 200 / vite 代理 GET 拿到真 401），Playwright 复现：点击后 `GET /api/qa/sessions/{id}` 200 拿到数据但渲染抛 `TypeError: Cannot read properties of undefined (reading 'replace')`，Vue 渲染函数崩、消息区空白=「点不开」。换浏览器纯属巧合。

**根因**：3b41e7a（11-22 质检修正批#2，8093 编号渲染链）给有序列表正则加编号捕获组时，把 `markdown.ts` 三处 `listItem[1]` 一刀切成 `listItem[2]`——有序项（编号组+内容组）正确，**无序项（单捕获组）的 `[2]` 是 undefined**，任一 `- ` 列表项让 QaView 整条渲染崩。同批 section.ts 的同款改动处理是对的（注释明写「UL 内容仍在 m[1]」），只有 markdown.ts 中招。11-22 后没有 UI 真聊会话，直到 11-25 点历史才撞上。

**产物**：
- `src/utils/markdown.ts`：`itemText = ordered !== null ? ordered[2] : bullet?.[1] ?? ''`，三处渲染换用
- `../scripts/probe_qa_render_crash.mjs`（回归护栏）：内置 5 例必跑（无序/有序/`start="3"` 续号/嵌套），真实消息 dump 可选（psql 生成命令在文件头）

**怎么验的**：
- `node scripts/probe_qa_render_crash.mjs` → 内置回归 5/5 PASS；52 条真实消息（7095 全部+6802 对照）崩溃 0（修复前 4 条崩）
- 浏览器端到端（Playwright chromium）：登录→侧栏 44 条→点击 7095/7094 各渲染 50 条消息→`?session=6802` 渲染 2 条→零 pageerror
- `pnpm exec vue-tsc --noEmit` 0 错误
- 途中顺手修：主人 wedged 的 `fastapi dev` 后端（healthz 超时/登录 500）杀干净重启，日志落仓库外 %TEMP%——WatchFiles 连 backend/tmp 都 watch（日志实锤「detected changes in 'tmp\...'」），探针写一个 reload 一次

**已知限制 / 挂账**：前端仍零单测——markdown.ts 这类回归只有探针护栏兜着；「同款改动多处落地必须每处同测」教训（section.ts 有探针罩、markdown.ts 裸奔三天）。

## 2026-11-25 · 笔记页目录缺「纯容器型 H1」条目修复（9353 实锤：只有一和四）· 执行者：ZCode

**为什么做**：主人报 note 9353 左侧目录一级标题只有「一」「四」，二/三/五缺失，但其下二级 2.x/3.x 都在。DB 核对：doc 里五个 H1（一~五）+ 全部 H2/H3 完好；父块 heading_path 也全带 H1 前缀——真凶在 TOC 构建器。

**根因**：`NoteView` 目录是「每个父块一条」，label 取 heading_path **最后一段**。H1 下有正文的一/四有独立父块（条目=H1）；而二/三/五是**纯容器型 H1**（标题下无正文直接接 H2），chunker 把它们折叠成 2.x/3.x 父块的路径前缀——TOC 便永远看不到它们。老结构语料（H1 通常自带引言）没暴露过这个形态，9353（重导产的新结构）撞上了。

**产物**：`src/views/NoteView.vue` 目录改为**逐段展开**——每个父块的 heading_path 各段都生成条目（按路径前缀去重，已有独立父块的层级不重复出现）；key 改 `p{chunk_id}-{depth}`（同父块多条，裸 chunk_id 会撞 key）；高亮只点叶子（`isLeaf`，免得「二」和「2.1」同时亮）。

**怎么验的**：
- note 9353：目录 22 条，一~五全在，1.1/2.x/2.3.1（H3 更深一级）/3.x/4.x/5.x 层级完整，零 pageerror
- 老 note 161 回归：57 条零重复，「第一阶段：Agent 基础认知」这类同样缺失的一级也回来了
- note 4986 目录 0 条=笔记本就不存在（DB 0 行），非回归
- `pnpm exec vue-tsc --noEmit` 0 错误

## 2026-11-25 · 统一工作台改版（来源感知返回 + 删出处坞 + 会话列表瘦身）· 执行者：Trae 新会话

**为什么做**：主人对 UI 提了两条原则性意见——①信息不许重复：每条回答下方已有内联「出处 N 条」，右栏 `SourceDock` 出处坞是同一信息的第二次出现，删；②返回逻辑不许写死：笔记页返回按钮硬编码 `router.push({name:'notes'})`，从问答点引用跳过来也被带回书架。讨论后主人拍板「统一工作台」结构：书架/问答/视频共用一个框架，内容舞台 keep-alive 保活对话，返回零成本。

**产物**：
- `src/App.vue`：重构为三形态外壳（未登录裸渲染 / 引导期单入口顶栏 / 工作台）。工作台 = 极简顶栏（品牌+用户区，平铺导航全部移除）+ 左侧「书立」导航栏（书架/问答/视频/设置+底部我的，手写线条图标，激活=一锭墨）+ 内容舞台（qa/notes/video 放宽 1440px，其余 1060px 书页宽）；keep-alive 语义不变
- `src/router/prevRoute.ts`（新）：`shallowRef` 存上一条应用内路由；单独成模块是因为 router/index.ts 静态 import 全部视图，放那儿成循环依赖
- `src/router/index.ts`：`router.afterEach` 写入 prevRoute（首次加载 from 是 START_LOCATION，保持 null）；路由表零改动
- `src/views/NoteView.vue`：返回按钮来源感知——qa→「← 返回对话」并 `router.back()`（问答现场原样恢复），notes→「← 返回书架」，note 链→「← 返回笔记」…；prevRoute 为 null（刷新/外链）兜底「← 回书架」RouterLink
- `src/views/QaView.vue`：删 `SourceDock` 导入/挂载/dock 全套状态（dockMessageId/dockActiveN/dockCitations/resetSourceDock 及 3 处调用）；正文 [n]/[W n] 徽章点击改为直接 `openCitation`（库内笔记本页打开=keep-alive 保住对话，联网出处开新窗）；删「问答」大标题（书立已标明位置）；会话列表加搜索框（标题过滤）+ 导出/删除按钮改悬停/焦点浮现（触屏常显兜底）；新对话空态（三行引导：/检索、📎、/ 命令）
- `src/components/SourceDock.vue`：删除（回收站）

**怎么验的**：`pnpm build`（vue-tsc --noEmit + vite build）通过，2213 modules，19.2s；chunk>500kB 警告为 mermaid/elk 依赖既有现象，与本次无关。前端无单测（历史挂账，见上批），交互验证靠主人浏览器实测。

**已知限制 / 挂账**：①书立 sticky 顶距 53px 与顶栏高度（52px+1px 边框）硬编码联动，改顶栏高度要同步；②「返回对话」依赖 keep-alive 恢复问答现场，行为=回到离开时的滚动位置与消息，不做逐条动画回放；③触屏无悬停场景仅做了常显兜底，未做移动端布局。

## 2026-11-25 · 书架页精修（删常驻红字 + 轻翻页 + 抽书动效）· 执行者：Trae 新会话

**为什么做**：主人逐页精修第一批选定书架。满架 36 张书卡每张下面常驻一个「删除」文字按钮，是全站最大的视觉噪音（与上一批删会话列表常驻按钮同性质）；大标题「笔记库」在书立导航下是重复信息；数字页码一排按钮的重量感与纸面书架不配。

**产物**（全在 `src/views/LibraryView.vue`）：
- 模板：去 `page-head` 大标题（搜索行升为页头）；删除按钮从卡下 meta 行移除，改为盖在封面左上角的 22px 圆形小 ×（悬停整本书/键盘聚焦浮现，触屏常显兜底，确认弹窗与乐观移除逻辑不变）；翻页改「上一页 · 第 x / y 页 · 下一页」文字链
- 脚本：`pageItems`（页码窗口 computed）随数字页码一起删除
- 样式：`.book` 加 `position:relative` 锚点；`.book-del` 重写为圆形浮层钮；`.book-link:hover .book-cover` 加 `rotate(-0.5deg)` 抽书轻旋；`.book-meta-right` 删除；`.pager` 系列改文字链风，`.pager-num*`/`.pager-gap` 删除

**怎么验的**：`pnpm build`（vue-tsc --noEmit + vite build）通过；grep 确认无 pageItems/pager-num/book-meta-right 残留引用。交互（× 浮现时机、删除确认）待主人浏览器实测。

**已知限制 / 挂账**：①封面左上 × 与书脊色条相邻（spine 6px、× 在 8px 起），极浅色书脊下对比一般，主人实测不满意再调；②「删除中…」文字态改为 title 提示+按钮半透明，删除进行中无文字进度。

## 2026-11-25 · 书架双视图（5:7 比例 + 横排题签 + 列表视图）· 执行者：Trae 新会话

**为什么做**：主人实测书架后反馈两点——①书的比例不对（旧卡高写死 186px、宽随列自适应，1440 舞台下约 250×186，**宽比高长**像明信片）；②标题几乎全部省略（竖排题签一条只能装 8~10 字，标题普遍 12~20 字，载体装不下）。主人拍板：题签改横排（现代书封面本以横排为主），并接受双视图方案——书卡管氛围、列表管查找，切换钮不算多余设计，因为它解决的是「形态 vs 全名」的真实矛盾而非信息重复。

**产物**（全在 `src/views/LibraryView.vue`）：
- 比例：`.book-cover` 高度 186px → `aspect-ratio: 5/7`；`.shelf` 固定 5 列 → `auto-fill minmax(190px, 1fr)` 自适应（1440 宽约 6 列），三段窄屏媒体查询随之删除
- 题签横排：`.book-title` 去掉 `writing-mode: vertical-rl`，改顶部横排白签（left 18 避书脊 / right 12 / top 12），衬线 13px 居中，两行 `-webkit-line-clamp` 截断 + `:title` 悬停全名
- 列表视图：`.shelf-rows` 行条目——书脊色条（沿用 `spineColor`，延续隐喻）+ 全名（两行 clamp）+ 质量/段数/日期 + 行尾悬停删除 ×（与书卡 × 同语言）；同一分页/搜索/拖放导入全复用
- 切换钮：搜索行内「书架/列表」两个 28px 图标钮（active 一锭墨）；`view` 状态 localStorage `baleen-shelf-view` 记住，默认书架；`GRID_COLS/GRID_ROWS` 常量删除，`PAGE_SIZE` 直书 30

**怎么验的**：`pnpm build`（vue-tsc --noEmit + vite build）通过；grep 确认 GRID_COLS/writing-mode/pager-num/book-meta-right 零残留。书卡比例与横排效果待主人浏览器实测。

**已知限制 / 挂账**：①横排题签 2 行约 30 字，超长标题（>30 字）仍省略，靠悬停或列表视图；②列表行标题也是 2 行 clamp，3 行以上的超长名同样靠 title；③书卡高度随列宽变化（190px 列宽时约 266px 高），主人若嫌一屏行数少，把 minmax 调小即可加密。




## 2026-11-25 · 书架批量删除 + 题签单行滚动 · 执行者：Trae 新会话（主人拍板）

**为什么做**：主人两条实测反馈——①「鼠标触碰显示删除按钮的设计不好」，要改成点「批量删除」后进入点选模式统一删，而不是悬停浮现 ×；②题签两行截断出现「文字只露出上半截」的怪相（line-clamp 与 max-height 在窄卡上裁切异常），要求改单行、悬停时滚动展示全名。

**产物**（全在 `src/views/LibraryView.vue`）：
- 批量删除：搜索行加「批量删除」钮（激活态浅青）→ 进入选择模式显示操作条（已选 N 本 · 朱砂「删除所选」· 取消）；书卡/列表行整体点击即选中切换（`onCardClick` 在批量模式下 preventDefault 拦掉跳转），左上角空心圈/实心勾角标标示；`removeSelected` 逐本删、部分失败保留现场可重试，全部成功才退出；确认弹窗文案带数量
- 删除 hover ×：`.book-del`/`.row-del` 按钮及全部 CSS（含 hover:none 媒体查询）删除；脚本 `deletingId`/`removeNote` 删除
- 题签单行：`.book-title` 去 `-webkit-line-clamp`/`max-height`，改 `white-space: nowrap` 单行 + 右缘 12px mask 渐隐（硬截半个字的怪相消失）；新增本地指令 `v-title-scroll` 实测每条题签是否溢出，溢出才挂 `book-title--overflow` 类并把滚动位移/时长（约 60px/s，最少 3s）写入 CSS 变量；hover 时 `title-scroll` 动画首尾各停 12% 拍再折返；`:title` 原生提示保留
- 样式新增：`.book-check`/`.row-check` 角标、`.book--selected .book-cover` 黛青描边圈、`.book-row--selected`、`.batch-bar`/`.batch-btn`/`.batch-del-btn`/`.batch-cancel`

**怎么验的**：`pnpm build`（vue-tsc --noEmit + vite build）通过（exit 0，19.2s）；grep 确认 deletingId/removeNote/book-del/row-del 零残留。滚动观感与批量流程待主人浏览器实测。

**已知限制 / 挂账**：①题签滚动时长按整签宽统一估算（scrollWidth/60），首尾停拍比例固定 12%，不按字数精确分段；②列表行标题仍两行 clamp（行高充裕无裁切怪相，不动）；③批量选择跨页保留已选 id，翻页/搜索后计数不变，删除时逐本生效。

## 2026-11-25 · 书架批量选中交互修复（capture 拦截 + 选中态加重）· 执行者：Trae 新会话（主人实测反馈）

**为什么做**：主人实测批量删除两处不便——①批量模式下点书还是跳转进了笔记（根因：RouterLink 在 a 元素自己的 click 监听里就同步 `router.push`，冒泡到 li 再 `preventDefault` 为时已晚）；②选中卡与普通卡差异太弱，看不出选没选上。

**产物**（全在 `src/views/LibraryView.vue`）：
- `onCardClick` 拦截从 `@click` 改 `@click.capture`（书卡 li 与列表行 li 两处）：捕获阶段先 `preventDefault`，vue-router 的 `guardEvent` 见 `defaultPrevented` 放弃跳转 → 批量模式下点书任意位置（封面/题签/meta）都变成勾选，不用专门点圆圈
- 选中态加重：勾角标 20→24px、描边改 ink-3 更醒目；选中封面蒙一层黛青纱（`::after` rgba(61,90,115,.22)）+ 白边黛青实心勾带投影；选中列表行加 1.5px 黛青外圈 + 行内勾变实心

**怎么验的**：`pnpm build`（vue-tsc --noEmit + vite build）通过。批量点选/取消、跳转拦截待主人浏览器实测。

**已知限制 / 挂账**：批量模式下 Ctrl/Shift 加点也会被拦（一律按单选切换处理），需要多选范围操作再说。

## 2026-11-25 · 纸篓(桌面回收站隐喻)· 执行者：Trae 新会话(主人拍板)

**为什么做**：主人要一个「有趣的复杂交互」——右下角悬浮纸篓,书可以按住拖进去,但不是直接删(像桌面回收站);点纸篓进去看列表,里面才有二次确认的真删除;批量「删除所选」时选中的书要飞入纸篓;批量删除按钮大小与「导入笔记」一致。

**产物**：
| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `src/stores/trash.ts` | 纸篓 store(ids/flying + localStorage `baleen-trash-ids` 持久化)。前端级回收站:入篓=书架隐藏,后端记录原封不动;彻底删除才走真删除 API |
| 新增 | `src/utils/spine.ts` | SPINES 八色 + spineColor 从 LibraryView 抽出(书架/纸篓/飞纸共用同一本书同一条色) |
| 新增 | `src/utils/flyPaper.ts` | flyToBasket():从源元素/矩形位置放飞纸片(临时 DOM,.fly-paper),transform 过渡落进 `#trash-basket`,Promise resolve=落篓 |
| 改 | `src/styles.css` | `.fly-paper` 全局样式(挂 body 下的元素进不了 scoped):28×36 小纸+左缘书脊色,飞完缩小旋转淡出 |
| 改 | `src/App.vue` | 工作台壳加 `#trash-basket` 悬浮钮(fixed 右下 22px,z-index 1100 全站顶层):线条纸篓图标+朱砂数量角标;dragover 张嘴(scale1.14+浅青)、drop 读 dataTransfer 的 id/spine/源矩形→书架卡淡出→纸片飞入→pulse 弹一下→入账;点击 push `/trash` |
| 改 | `src/router/index.ts` | 加 `/trash`(name trash,静态 import) |
| 改 | `src/views/LibraryView.vue` | 书卡/列表行 `draggable` + `@dragstart` 序列化(id/spine/源矩形);「删除所选」改为 trashSelected:纸片逐张错峰 70ms 起飞、卡片原地淡出(`.book--flying`,trash.flying 驱动),全部落篓才 trash.add,不再弹确认(移入可逆);v-for 换 `visibleNotes`(滤已入篓),计数显示「…/N · 纸篓 M」,空书架文案区分「纸篓里还有 M 本」;`.batch-btn` 加 min-width 118px 对齐导入钮;本地 SPINES 删除改用 utils |
| 新增 | `src/views/TrashView.vue` | 纸篓页:标题+回书架;逐条 getNote 拉详情(404 悄悄出篓防幽灵条目);每行=书脊条+标题+段数+「放回」「彻底删除」(btn--danger,confirm 二次确认);「放回全部」一键后悔;空态文案 |

**怎么验的**：`pnpm build`(vue-tsc --noEmit + vite build)通过(2219 modules,20.7s)。拖拽入篓/飞纸动画/纸篓页放回与彻底删除待主人浏览器实测。

**已知限制 / 挂账**：①纸篓只记在当前浏览器(localStorage),换浏览器/清缓存即空,也不跨设备同步;②入篓笔记后端仍在:问答检索在彻底删除前仍可能命中(纸篓页有文案明示);③批量模式与拖拽并存时,批量选中的书也可以拖(拖走的不清选中态,下次删除所选会带着它的 id,但落篓 add 有去重,无副作用);④trashSelected 用固定等待(错峰×N+620ms)对齐动画,不逐张等 Promise——张数极多(>50)时尾部纸片可能还在飞就入账,视觉误差可忽略。

## 2026-11-25 · 纸篓二次打磨(立体篓体/自定义拖影/弹窗化)· 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人三条反馈——①纸篓长得像按钮,要一只立体的纸篓;②拖拽时是浏览器默认半透明快照(像拖着个链接),要「书被从架上拉下来」的效果;③点纸篓不该切页面,应从页面中央弹悬浮窗+朦胧遮罩,点遮罩收回。

**产物**:
| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `src/components/BasketModal.vue` | 纸篓弹窗:中央悬浮窗(衬线标题/放回全部/×),遮罩 rgba(28,26,22,.42)+backdrop blur 3px,`@click.self` 点遮罩收回、Esc 收回;列表=放回/彻底删除(二次确认),逻辑自 TrashView 移植 |
| 改 | `src/App.vue` | ①篓体重绘为立体 SVG(64×68:布面渐变梯形篓身+三根竹篾+两道箍+深色篓口内洞+浅色前沿高光弧+地面影;篓里有书时篓口冒出一张皱纸头);②容器去按钮感(border/background 全去,drop-shadow 落地);③点击改弹窗(basketOpen),不再 router.push;④drop 纸片改从松手点(clientX/Y)继续飞进篓口,不再从书架原位飞 |
| 改 | `src/views/LibraryView.vue` | dragstart 自定义拖影:临时建 `.drag-book-ghost`(48×66 小书,左缘书脊色+衬线题签,rotate -3°)setDragImage 跟手,dragstart 同步捕获后即摘;draggingId 原位浅影(半透明+微缩+轻旋,像书被拎起来),dragend 清理 |
| 改 | `src/styles.css` | `.drag-book-ghost` 全局样式(挂 body 下的快照元素) |
| 改 | `src/router/index.ts` | 删 `/trash` 路由与 TrashView import(纸篓弹窗化后不再占页面) |
| 删 | `src/views/TrashView.vue` | 逻辑整体迁入 BasketModal,页面形式退役(回收站) |

**怎么验的**:`pnpm build`(vue-tsc --noEmit + vite build)通过(2219 modules);grep 确认 TrashView/`/trash` 零残留。立体观感、拖影手感、弹窗遮罩交互待主人浏览器实测。

**已知限制 / 挂账**:①SVG 渐变 id(`basket-body`/`basket-hole`)是全局文档级 id,当前页面只有一份篓体,无冲突,但若同页渲染多只篓需改 id;②自定义拖影在部分浏览器(如旧 Safari)对 setDragImage 离屏元素支持不一致,本机 Chrome/Edge 实测路径为准;③拖影快照无法继承 CSS 动画,书「被拉下来」的动感靠 ghost 造型+原位浅影+落纸动画三段接力表达。

## 2026-11-25 · 纸篓迁入书立 + 苹果风毛玻璃 · 执行者:Trae 新会话(主人拍板)

**为什么做**:主人两条——①纸篓位置应在「我的」按钮上面(不再悬浮在右下角内容区);②外观改成苹果风格。

**产物**(全在 `src/App.vue` 模板/样式 + 两处文案):
- 位置:篓体从 fixed 右下角移入 `.rail-foot`,`<button id="trash-basket">` 排在「我的」RouterLink 上方(flyPaper 靠 `getElementById('trash-basket')` 定位,挪位置零成本)
- 外观:56×56 苹果风毛玻璃瓷片——半透明白底 rgba(255,255,255,.38) + `backdrop-filter: blur(8px)` + 1px 白描边 + 顶部 inset 内高光 + 柔和投影;hover 轻浮起提亮,拖拽悬停放大提亮,落纸 pulse 弹动保留;朱砂数量角标保留
- 立体 SVG 篓体原样保留(布面渐变/竹篾/深色篓口/冒纸头),缩放为 44×46 坐进瓷片
- 文案同步:BasketModal 空态「右下角」→「左下角书立里」;flyPaper 注释同步

**怎么验的**:`pnpm build`(vue-tsc --noEmit + vite build)通过(2219 modules,19.8s)。书立内观感与拖拽落篓链路待主人浏览器实测。

**已知限制 / 挂账**:backdrop-filter 毛玻璃在极旧浏览器退化为纯半透明白(不影响功能);书立底色较浅,毛玻璃对比偏含蓄——主人若觉得不够「浮」可再加投影或提高白底不透明度。

## 2026-11-25 · 视频页「投喂台」改版(方案 A)· 执行者:Trae 新会话(主人拍板)

**为什么做**:主人逐页精修第二轮选定视频页。现状三宗罪——「视频笔记」大标题与书立重复;没粘链接时页面只有一根输入条,冷启动死气沉沉;右侧 340px 设置栏为两个模式单常驻,分量过重。草图对比后主人拍板方案 A「投喂台」。

**产物**(全在 `src/views/VideoView.vue`,纯模板/样式,脚本零改动):
- 去大标题;布局从「左播放器+右 340px 设置」双栏改单列居中(`.feed` max-width 860,舞台虽 1440 档但页面自收)
- 投喂台:空态=52px 高居中大输入框(placeholder「粘贴 B 站或抖音链接,喂给书房…」)+ 两张并排模式小卡(whisper 深度/字幕快速,整卡可点,radio 隐身保语义,选中黛青描边+浅底+✓)+ 导入钮(260px 居中)+ B 站登录收成一行小字+文字链扫码
- 粘了链接:投喂台收成常规尺寸,下方原位展开 16:9 播放器(或抖音外链预览/无法识别提示)+ 导入进度卡
- 删旧样式:`.video-layout`/`.player-section`/`.settings-section`/`.url-bar`/`.import-card`/`.mode-*` 旧体系/`.bili-login*`;新增 `.feed`/`.tray*`/`.mode-opt*`/`.bili-link`
- 播放器/抖音预览/进度面板(含白盒日志)逻辑与结构原样保留

**怎么验的**:`pnpm build`(vue-tsc --noEmit + vite build)通过(2219 modules,18.1s)。空态观感与粘贴后展开链路待主人浏览器实测。

**已知限制 / 挂账**:①空态仅在没有链接且没有进度面板时出现(导入中清空链接不会误回空态);②抖音链接时「字幕快速」卡降透明度并禁点,但卡片文案未动态变灰说明,靠 desc 里「仅 B 站」提示。

## 2026-11-25 · 视频页投喂台布局修正(输入行+滑块分段)· 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人看后觉得卡片纵排布局奇怪,指定两点——①导入笔记按钮放输入框右边,整行与播放器同宽;②两种导入方式改「点击滑动切换」。

**产物**(全在 `src/views/VideoView.vue` 模板/样式,脚本零改动):
- 第一行 `.tray-bar`:输入框(flex:1)+ 导入钮同排,整行与播放器同宽;空态输入框降到 44px 高(去居中文字)
- 第二行 `.tray-row2`:左侧 `.mode-switch` 胶囊分段控件——grid 两段等宽,`.mode-thumb` 墨色滑块 translateX 滑动(0.18s),选中段文字反白;radio 隐身保语义;抖音时「字幕快速」段降透明度禁点并给 title 说明
- 右侧 B 站登录一行小字不变;删 `.mode-opt*` 卡片体系
**怎么验的**:`pnpm build` 通过(2219 modules,18.9s);滑动手感待主人实测。

## 2026-11-25 · 阅读页扉页头部(方案 A)· 执行者:Trae 新会话(主人拍板)

**为什么做**:逐页精修第三页(阅读页)。现状两处扎眼——编辑/删除按钮常驻标题右侧,阅读时打眼;元信息「质量: 语言: 标签: 来源 #」冒号连环像表单。三方案草图后主人拍板方案 A「扉页」。

**产物**(全在 `src/views/NoteView.vue`,纯模板/样式):
- 操作悬停浮现:`.note-actions` 默认 opacity 0,`.note-head:hover/:focus-within` 浮现;触屏(hover:none)常显 0.9。编辑/删除按钮本体不动
- 元信息一行点隔:去掉质量 badge 与「质量:/语言:/标签:/来源 #」冒号腔,改为纯文本 span 序列(`高 · markdown · 机器学习 / 笔记 · 来源 #483 · 12 节`),CSS `span + span::before` 自动加分隔点;标签改 `/` 连接
- 细墨线:`.note-head` 加 border-bottom + padding-bottom,阅读区从线下展开,扉页感
- 目录/正文/编辑器/灯箱零改动

**怎么验的**:`pnpm build`(vue-tsc --noEmit + vite build)通过(2219 modules,21.1s)。悬停浮现与触屏兜底待主人实测。

---

## 2026-11-25 · 设置页删重复章程卡 · 执行者:Trae 新会话(主人已批)

**为什么做**:章程入口在设置页和个人中心各一份,重复。主人已批准删设置页那份,章程入口统一收敛到个人中心(有预览+编辑跳转)。

**产物**(全在 `src/views/SettingsView.vue`):
- 模板:删整段 `.charter-section` 卡(标题/描述/「查看 / 修改章程」按钮)
- 样式:删 `.charter-section` `.section-title` 两个 scoped 类
- 脚本:删 `useRouter` import 与实例(删卡后无路由跳转,死代码清理)

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,21.10s)。

---

## 2026-11-25 · 个人中心重排(通栏+统计条形图+两栏) · 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人三条——右侧大片留白(卡片限宽 640px 而舞台 1060px)、使用统计四格数字不像统计、
「模型与偏好配置」名不副实(只有模型相关项,没有偏好项)。

**产物**(全在 `src/views/ProfileView.vue`):
- 通栏布局:去 `.card` 的 max-width 640px,三段填满书页宽——头像条 / 统计卡 / 下半 `.grid-2` 两栏(章程 | 模型配置),≤900px 降单列
- 统计条形图:四格数字换成横向条形图(纯 CSS,零新依赖)——`chartRows` 按数量降序,`barPct` 以最大值为 100%(0 不画,>0 保底 4%),黛青渐变墨条 + `scaleX` 进场动画逐行错峰,衬线体数字右对齐。接口只有 4 个计数没有时间序列,条形图是唯一不造假的图型
- 改名:「模型与偏好配置」→「模型配置」
- 顺手清理:删无引用的 `.hint` 死样式

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,19.42s)。条形图观感与两栏比例待主人实测。

---

## 2026-11-25 · 个人中心删使用统计卡 · 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人看完条形图版后拍板——使用统计整个多余,直接删。

**产物**(全在 `src/views/ProfileView.vue`):
- 模板:删整段「使用统计」卡(条形图)
- 脚本:删 `chartRows` `barPct`(图表专用,死代码清理),注释同步(「通栏三段」→「通栏两段」)
- 样式:删 `.chart-*` 全系与 `bar-in` keyframes
- 布局现为:头像条 + 章程|模型配置两栏,页面更轻

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,21.94s)。后端 /api/profile 的 stats 字段未动(前端不展示而已)。

---

## 2026-11-25 · 设置页通栏两栏改版 · 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人实测设置页布局不对称、右边空白——原 `.form` 卡限宽 560px,舞台 1060px,右侧空 ~500px。

**产物**(全在 `src/views/SettingsView.vue`,纯模板/样式,脚本零改动):
- 表单拆两栏(与个人中心同一套 grid-2 语言):左「模型服务」(网关地址/模型/API Key),右「联网与生成」(Tavily Key/上下文上限/输出上限/结构图开关),两卡等高
- 保存按钮+提示移出卡片,变底部通栏操作行 `.actions`(按钮 + 「已保存」提示同行)
- 新增 `.field--last`(卡内末字段不吃底边距,防止把卡撑高一截空);≤900px 降单列兜底
- 删 `.form` 限宽样式;`.section-title` 回归(拆卡后每卡需要标题)

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,18.95s)。两栏观感待主人实测。

---

## 2026-11-25 · 问答页对话列限宽居中 · 执行者:Trae 新会话(主人拍板排期)

**为什么做**:问答舞台 1440px 宽,侧栏 210px + 对话区撑满剩余 ~1200px——长答案行宽过宽(阅读舒适区约 70-80 字符),整体靠左。之前讨论过「对话列收窄 ~760px 居中」,本批落地。

**产物**(全在 `src/views/QaView.vue`,纯样式,脚本零改动):
- `.chat-main`:去 `flex: 1`,改 `width: 100% + max-width: 760px + margin: 0 auto`——对话列在侧栏之外的剩余空间里居中,两侧对称留白(ChatGPT 式);空态/消息列表/输入框/缓存状态行全部随列同宽
- 侧栏(210px)与舞台宽(1440px)不动;文件头注释同步

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,18.19s)。列宽 760px 的观感待主人实测,嫌窄/嫌宽只调 `max-width` 一个数。

---

## 2026-11-25 · 问答页「眉批手帐」版式 · 执行者:Trae 新会话(主人拍板,四方案选一)

**为什么做**:主人实测「整体布局太松散」,点名要「不寻常的设计」。四方案(眉批手帐/卷轴横卷/书问书答/只收紧)草图后主人选「眉批手帐」:整页一张摊开的宣纸,彻底消灭「侧栏/对话列/输入框三座孤岛」的松散感。

**产物**(全在 `src/views/QaView.vue`,逻辑零改动——消息数据/流式/附件/斜杠命令/出处跳转全不动):
- 纸面:`.qa-layout` 挂 `.manuscript`(panel 纸色大圆角 + 阴影,max-width 1240px 居中在桌面上)
- 左书签绳:会话列表去卡片化,左侧竖虚线作「绳」,每条会话挂结点小圆,当前会话系朱砂结;侧栏 210→168px
- 正文区:AI 回答去气泡框,文章式排版直接落纸,首行墨色「答」章;「思考中」也落正文列
- 右眉批:用户提问改眉批体——衬线小字 + 右缘朱砂批线 + 朱砂「问」章,引用/附件 chips 跟在批注后(浅黛青牌)
- 交错下行:`.msg-list` 气泡流改 `.msg-weave` 两列 grid(左正文 1fr / 右眉批 236px),每条消息按时间序占一行,`:style` 显式 gridRow/gridColumn 定位,问答交错自然下行,无需分组算法
- 砚台墨带:输入区整条墨底(input-shell 墨化),chips 反色成墨上半透明纸牌,📎 幽灵键,发送钮纸白反转(墨带上唯一亮钮)
- 顺手:删死样式 `.chat-toolbar`;文件头注释重写

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,22.27s)。眉批/墨带/书签绳观感与交错阅读节奏待主人实测。

---

## 2026-11-25 · 问答页书签绳滚动条修正 · 执行者:Trae 新会话(主人截图反馈)

**为什么做**:主人截图反馈会话栏「有部分内容被遮住」——实为会话列表用了 Windows 原生滚动条(粗轨道+上下 ▲▼ 箭头按钮),压在列表右缘,视觉上像内容被裁;且侧栏 168px 偏窄,标题省略严重。

**产物**(全在 `src/views/QaView.vue` 样式):
- `.session-list` / `.msg-weave`:自定义细墨滚动条(6px 圆角墨灰、无箭头、透明轨道,`scrollbar-width: thin` + `::-webkit-scrollbar` 双写),两处滚动区观感统一
- `.session-side`:168 → 190px,标题省略缓解、悬停按钮不再局促

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,20.69s)。原生 ▲▼ 粗滚动条消失,待主人刷新确认。

---

## 2026-11-25 · 问答页破盒出血 + 降饱和 · 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人两条——整体颜色太刺眼(整条墨带/黑钮大面积深色)、所谓手帐只是「把内容装进一个盒子」,周边仍是大片桌面空白。

**产物**:
- `src/App.vue`:新增「出血」舞台形态——qa 路由时 `stage--bleed`(撤 padding)+`stage-inner--bleed`(撤 1440 限宽,flex column 撑满);isWide 里 qa 移到 bleed,书架/视频仍走 --wide。其他页面零影响
- `src/views/QaView.vue`:
  - 破盒:`.manuscript` 去边框/圆角/阴影/1240 限宽,纸面铺满视口剩余区域(顶栏以下整页是纸,不再有桌面空白);高度链 stage→qa-page→manuscript→msg-weave 全 flex 贯通,消息区从固定 56vh 改为 flex 自适应——屏幕越高可见对话越多,空态同享
  - 降饱和:砚台墨带 → 浅纸墨槽(bg-soft 凹槽+描边,聚焦黛青光);输入文字/chips/📎 恢复浅色系;发送钮回归全局墨钮(小面积强调);「+ 新对话」黑钮 → 浅黛青;眉批朱砂批线 2px 实色 → 1.5px 40% 淡朱砂
- 文件头注释同步

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,19.25s)。出血铺满后的沉浸感与降饱和后的观感待主人实测。

---

## 2026-11-25 · 问答页单列对话流 + 输入栏钉底 · 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人两条——① 输入栏要拉到页面最底才能看到(高度链断:app-shell 是 min-height,stage--bleed 只撤留白没钳高度,长回答把整页顶长、窗口级滚动);② 眉批双列让提问挤在 236px 窄列、问与答视觉断裂、右列长期空白,与书架页气质不统一,要「简约大气」。讨论后主人拍板单列方案,并要求思考状态复用空间。

**产物**(逻辑零改动——消息数据/流式/附件/斜杠命令/出处跳转全不动):
- `src/App.vue`:`.stage--bleed` 补 `height: calc(100vh - 53px)` + `min-height: 0`(53px = 顶栏 52+1),`.stage-inner--bleed` 补 `min-height: 0`——高度链贯通,消息区内滚、输入栏永久钉底,其他页面零影响
- `src/views/QaView.vue`:
  - 单列对话流:`.msg-weave` 两列 grid → flex column,max-width 800px 居中(内容 ~760px);问答同列按时间垂直下行
  - 用户提问:右对齐轻量块(`align-self: flex-end` + max-width 82%),衬线 + 右缘 2px 淡朱砂细线(眉批语言收进单列),引用/附件 chips 随块右对齐;模板删掉 gridRow/gridColumn 行内定位
  - 思考状态:从正文流的临时占位 leaf 移到输入区上方常驻状态行(`.thinking-strip`),与缓存状态行同区;`.thinking-hint` 改行内;删不再用的 `.leaf--pending`
  - 底区对齐:输入槽 / 思考行 / 缓存行 / 存笔记选项卡全部 max-width 800px 居中,与对话列同一条视觉中轴
  - 会话侧栏 190 → 176px;文件头注释重写

**怎么验的**:`pnpm build` 通过(vue-tsc 零错误,19.31s)。Playwright 实测 1440×900:50 条消息的长会话下 `document.documentElement` 不可滚动(window_scrollable=False),输入框固定在 y≈814 视口内;消息区滚到底输入栏仍在。观感待主人实测。

---

## 2026-11-25 · 问答页对话列放宽通栏 · 执行者:Trae 新会话(主人实测反馈)

**为什么做**:主人实测单列方案后反馈「聊天框还可以大,两边空白拉到最小,不是一点不留」。

**产物**(均在 `src/views/QaView.vue`,纯样式):`.msg-weave` / `.chat-input` / `.thinking-strip` / `.cache-status` / `.note-plan` 去掉 800px max-width 居中,改通栏 + 两侧 24px 呼吸边距;文件头注释同步。

**怎么验的**:`pnpm build` 通过(19.59s);Playwright 1440×900 实测窗口不可滚、输入栏钉底(y≈814),内容区铺满。观感待主人实测。

## 2026-11-25 · 验收后三处修复(搜索计数 / 笔记页双层滚动 / 无障碍底座) · 执行者:ZCode

**为什么做**:主人自己完成工作台改造后我验收,实测抓出 2 个真 bug + 2 处横切缺口。

### 产物(4 文件,全在 frontend/)

| 文件 | 改动 |
|---|---|
| `src/views/LibraryView.vue` | `visibleTotal` 加条件:无搜索词才减纸篓数。原来 `total - trash.ids.length` 在搜索时把「命中 2 条」减成 0(实测显示 `1–2 / 0`) |
| `src/styles.css` | ① 新增 `--chrome-h: 53px` 变量(顶栏 52px + 1px 分隔线);② 全站 `:focus-visible` 朱砂焦点环 + 输入框/墨底控件例外;③ `prefers-reduced-motion` 兜底 |
| `src/App.vue` | 新增 `.stage--clamp` / `.stage-inner--clamp`:笔记页路由下把舞台高度钳在视口内;`.stage--bleed` 的魔数改用 `--chrome-h`;窄屏 900px 放开 |
| `src/views/NoteView.vue` | `.note-page` 的 `height: calc(100vh - 96px)`(魔数,顶栏 53+舞台内边距 82=135,差了 39px)改 `flex:1`,高度由父级提供 |

### 关键根因

笔记页双层滚动的真因不是 NoteView 一个数写错,而是**高度约束链断在舞台层**:`.stage` 只 `flex:1` 没有确定高度,`.note-page` 用 `100vh` 硬算 → 与顶栏+内边距实际占用不符,页面多滚 39px。修法是把约束提到祖先(`.stage--clamp`),子级只负责占满(`flex:1`),不再各算一份视口魔数 —— 与 v33「不引视口魔数」决策一致,`--chrome-h` 让 53px 只定义一次。

### 怎么验的

Playwright 探针(口径同 `verify_fixes.py`,真后端 8000 + 真库,`--no-proxy-server` 绕系统代理),**11/11 PASS**:

- T1 搜索计数:`1–2 / 2 · 纸篓 3`,实见卡 2 ✓
- T2 笔记页 `scrollHeight == innerHeight == 900`(溢出 0px),正文栏自身仍可滚(contentH 631)✓
- T3 Tab 到导航得 `outline: 2px solid rgb(176,58,46)`;输入框 `outline: none` 不双环 ✓
- T4 `reduced_motion="reduce"` 下 transition 压到 `1e-05s` ✓
- T5 问答/设置/个人中心零滚动,1280 窄屏笔记页零滚动,零 JS 报错 ✓

**灵敏度验证(证明探针非假绿)**:撤回 `visibleTotal` 修复 → T1 FAIL(`1–2 / 0`);关掉 `stage--clamp` → T2 FAIL(溢出 12351px)。两处都能被探针抓到。

`pnpm exec vue-tsc --noEmit` → 0 错误。

### 环境坑(记录)

系统代理开启时 Playwright chromium 访问 `localhost:5173` 会 `ERR_CONNECTION_RESET`/超时(curl 5ms 就回 200,只有浏览器卡),launch 加 `args=["--no-proxy-server"]` 解决。

### 已知限制

- 焦点环只覆盖 CSS 能触达的元素;`<details>/<summary>`(出处折叠)仍是浏览器默认焦点样式
- `prefers-reduced-motion` 用通配 `*` 压时长,第三方库内部以 JS 驱动的动画(mermaid 渲染过程)不受控
- 视频页 656px 空白、会话侧栏 176px 下 86% 标题截断 —— 验收时提出,主人决定下一批处理

## 2026-11-25 · 体验补齐批（纸篓语义/侧栏/视频页/确认框/思维导图） · 执行者:ZCode

**为什么做**:主人拍板「全部按你推荐的做」。上一批验收留的 4 项挂账 + 思维导图三选一的 A+B。

### 一、思维导图改道 markmap（B 方案）

**问题**:问答页的 mermaid `mindmap` 难看(neutral 主题下根节点以下涂成单色)+ 灯箱放大后拖不动。

**改法**:
1. 新增 `src/utils/mindmap.ts`:```mindmap → markdown 嵌套列表 → markmap 渲染。懒加载(与 mermaid 同纪律),自带 fold / zoom / pan,不做灯箱。配色走纸墨(连线淡墨、节点浓墨、当前节点朱砂),变量是 markmap v1 的 `--mm-*` 契约,不污染全局。
2. 新增 `src/utils/mermaid.ts` 灯箱拖拽:pointerdown/move/up 平移 + cursor grab/grabbing + 位移 >4px 不算「点背景关闭」;滚轮缩放从「中心缩放」改为**以鼠标位置为锚点**;`zoom` 换成 `transform: translate() scale()` 一套坐标系(两者混用坐标会打架)。
3. `src/utils/markdown.ts:181-197`:围栏补 `data-lang`。**这是必修项** —— 问答页渲染链原本丢弃语言标记,新围栏在问答页根本不生效(笔记页 section.ts 早就带,两条链不一致)。
4. `src/utils/section.ts` 无需改(已带 data-lang),但笔记页也要接 renderMindmapsIn。
5. 后端提示词:`answerer.py:83-97` 拆开「流程/结构/关系 → mermaid」与「思维导图/知识拆解 → ```mindmap + 普通嵌套列表」;`summarizer.py:23` 围栏保留清单加 ```mindmap(否则「存为笔记」会把导图转成文字)。

**为什么不给 LLM 加工具**:导图是展示层问题。工具的价值是拿模型没有的信息,加导图工具只多一轮 tool-call 换零信息增益。业界(markmap/Kimi)同思路。

**新依赖**:`@markmap/parse` + `@markmap/render` + `@markmap/view` = 1.1.1。**刻意不装 `markmap-lib`**(741KB,捆绑 prismjs/highlight.js/markdown-it/katex,与项目已有 katex + 自研渲染器重复三份)。构建产物:markmap 落在独立懒加载 chunk(91KB),未进主包。

**旧会话兼容**:```mermaid mindmap 渲染路径完全保留,mermaid.ts 一行未删。

### 二、纸篓语义标注(A 方案)

纸篓是前端 localStorage,后端一行没动 → 问答检索仍会命中篓里的笔记。**不靠解释文字**,改在出处处挂「篓」标(`QaView.vue` `.cite-trash`,虚线灰签,`title` 说明"书架已隐藏,库中仍在,放回即恢复")。`QaView` 引入 `useTrashStore`。

### 三、会话侧栏(216→248px)

原来 176px 下 44 条会话 **38 条标题被截断(86%)**,11 条同名「TCP 三次握手…」完全无法区分。
- 侧栏 176 → 248px
- 会话行加**日期**(`shortDate` 用 `created_at`;后端列表契约无 `updated_at`,改它属 api.md 变更,故先用创建日)
- 导出/删除按钮从 `flex-shrink:0` 占位改为**绝对定位**(`opacity:0` 仍占 74px 宽度,这才是标题只有 99px 的真凶)

结果:标题可用宽度 99 → 189px(+91%),截断 44 → 26 条,余下均为超长标题(需 263px),属合理省略。

### 四、视频页空态

847px 舞台内容只到 244px(空 656px)。改为:整个投喂台垂直居中 + 空态显示**三步流程**(粘链接 → 自动转写 → 成一本书),用衬线汉字「一/二/三」编号,有链接或任务时整块消失。是「说清会发生什么」,不是标语。

### 五、确认框统一

新增 `src/utils/confirm.ts`(单例队列 + Promise<boolean>)+ `src/components/ConfirmDialog.vue`(纸墨风格、危险操作朱砂实底、Esc/点遮罩取消、打开即聚焦确认键)。替换 3 处 `window.confirm`:`BasketModal.purge` / `QaView.removeSession` / `NoteView.removeNote`。全库 `window.confirm` 归零。

### 怎么验的

两套 Playwright 探针(真后端 8000 + 真库,`--no-proxy-server`),**26 项全绿**:

**verify_fixes.py 11/11**(回归上一批):搜索计数 `1–2 / 2`、笔记页零溢出、焦点环 `2px solid rgb(176,58,46)`、reduced-motion 压缩、各页零滚动、零 JS 报错。

**verify_mindmap.py 15/15**(本批):
- 两条渲染链都带 `data-lang`(markdown / section / 普通代码块)
- `renderMindmapsIn` 真渲染出 SVG,markmap 画出节点文字 `['机器学习','监督学习','无监督学习','强化学习']`
- 灯箱:打开 → 滚轮缩放到 `scale(2.8)` → **真鼠标拖拽** `translate(0,0) → translate(90px,50px)` → 拖后灯箱不误关 → 点外框关闭 → Esc 关闭
- 确认框:弹出 / 朱砂危险样式 / 确认返回 true 并关闭 / 打开即聚焦确认键

`pnpm exec vue-tsc --noEmit` → 0 错误;`pnpm build` ✓ 42.39s;后端 `uv run pytest -q` → **696 passed, 1 xfailed**(基线未降);`doc_lint` 0 ERROR。

### 已知限制

- markmap 渲染失败的降级是「保留列表 + 角标」,与 mermaid/svg 同款;但 markmap 内部异常不会冒泡到我们的 catch 之外
- 会话列表仍只有 `created_at`(非最近活跃时间),因为后端 `SessionItem` 没透出 `updated_at`;要显示"最近活跃"需改 api.md 契约
- 纸篓仍是本浏览器 localStorage:换浏览器/清缓存即空。这是当前设计,不是 bug;若要跨设备需后端软删除
- 视频页三步流程是静态示意,不读取"最近导入"列表(那需要 GET /api/sources 前端封装,避免本批跨接口)

## 2026-11-25 · 问答空态砍三行提示，改「/」按钮（续 d13e365） · 执行者:ZCode

**为什么做**：主人指出空态那三行（`/检索…` / `📎…` / `/ 唤起全部命令…`）是多余额信息——它把输入框旁已有的按钮和 `/` 菜单又抄了一遍，属于 d13e365「砍标语/提示」批漏掉的一条。

**改法（改布局，不是改文案）**：
1. 删掉 `.qa-empty-tips` 整个列表（保留标题 + 一句副标题），连带清掉失效 CSS（`.qa-empty-tips` / `.qa-empty-tips b` 两条规则）。
2. 输入壳里新增 `.slash-trigger`：**空输入时露出的「/」钮**，点在输入框左侧。点了等于手打一个斜杠（写入同一 input → `updateSlash()` → 菜单弹出），后续方向键/Enter/Tab/Esc 路由与手打完全一致。有字时自动消失，清空后回来。
3. 输入框占位符 `问点什么…(输入 / 唤起命令)` → `问点什么…`（命令现在看得见，不用在占位符里教）。

结果：空态从「标题+副标题+三行说明」变成「标题+副标题」；屏幕上的操作入口全部是可见可点的实体（`/` 钮、📎 钮、发送钮），没有一句教用户怎么打字的文字。

**怎么验的**：Playwright(真后端真库)
- 空态 `innerText` = `问点什么吧\n\n关于笔记库的问题,或任何想整理的资料`（三行已消失）
- `.qa-empty-tips` 计数 **0**（模板与 CSS 双清）
- 点 `.slash-trigger` → `.slash-menu` 出现、input 值 = `/`、4 条命令全在（新对话/存为笔记/导出/检索）
- 有输入时 `.slash-trigger` 计数 0（自动隐藏），清空后回到 1
- 零 JS 报错；`vue-tsc --noEmit` 0 错误

**挂账**：`/检索` 与 📎 仍然只靠按钮自解释（`/` 钮的 `title="命令"`、📎 的 `aria-label`）。如果主人希望 `/` 钮更进一步（比如 hover 展开命令预览），那是另一个交互设计，未做。

## 2026-11-25 · 📎 附件钮移进输入壳（收尾上一条） · 执行者:ZCode

**为什么做**：上一条把「/」钮放进了输入壳，于是三个按钮散落三处（`/` 在壳内、📎 在壳外、发送在更外），视觉不齐。主人拍板一并收进壳内。

**产物**（`src/views/QaView.vue` 单文件）：
- 模板：`.attach-btn` 从 `<form>` 直下移入 `.input-shell`，排在 `.qa-input` 之后；加 `@click.stop`（避免冒泡触发壳的 `focusQaInput` 又抢焦点）。壳外现在只剩「发送」一个按钮。
- 样式：`.attach-btn` 从「带边框的独立键」改成**无框图标位**（26×26、无 border/背景、hover 只变底色和黛青色）。它不再需要自己的边框——输入壳的外框已经表达了「这是一个输入框」。

最终结构：`[ / ] [ 问点什么…____________ ] [ 📎 ]` ＋ 壳外 `[发送]`。

**怎么验的**：Playwright(真后端真库)
- 几何：`.slash-trigger` / `.qa-input` / `.attach-btn` 三者 `getBoundingClientRect` 全部落在 `.input-shell` 内、且垂直中心差 <6px（同一行）；`.chat-input` 直接子元素只剩 `input-shell` + 发送键（壳外无第三个按钮）
- 功能：点 `/` → 命令菜单出现；点 📎 → 真触发隐藏 `input[type=file]` 的 click（探针拦了默认行为以免弹系统框）
- 联动：输入文字后 `/` 钮隐藏、📎 仍在（附件任何时候都要能挂，与有没有打字无关）
- 零 JS 报错；`vue-tsc --noEmit` 0 错误

**已知限制**：📎 无边框后完全靠 emoji 本身辨识，在深色/高对比系统主题下 emoji 渲染差异较大；如观感不够可换成线性 SVG 回形针图标（与 `rail` 导航的图标语言统一）。

## 2026-11-25 · 会话行右键菜单 + 行内重命名 + 标题悬停滑动 · 执行者:ZCode

**为什么做**：主人拍板「把删除、导出会话和重命名做成右键显示列表」，并定了四条口径：**只允许右键触发**、删掉原来的导出/删除图标、双击会话可直接重命名、悬停标题动态滑动看全名。后端 `PATCH /api/qa/sessions/{id}` 已由另一会话落地（28b7df0，api.md v36）。

### 产物（3 文件，全在 frontend/）

| 文件 | 改动 |
|---|---|
| `src/api/http.ts` | 加 `patch<T>()`（原本只有 get/post/put/postForm/del） |
| `src/api/qa.ts` | 加 `SESSION_TITLE_MAX = 30` 常量（与后端 `TITLE_CHARS` 同口径）+ `renameSession(id, title)` |
| `src/views/QaView.vue` | 右键菜单 + 行内重命名 + 标题悬停滑动；删掉旧的两个悬停图标及其样式 |

### 设计与关键决策

1. **只右键触发**（主人拍板，推翻了自拟的「悬停 ⋯ 双触发」）：行内除标题+日期不再有任何常驻控件；探针断言「悬停不出现任何按钮」。
2. **菜单三项**：重命名 / 导出 Markdown / 删除，删除单独一组用 hairline 隔开、朱砂字。复用既有 `exportSession()` 与 `utils/confirm.ts`（朱砂危险款）。
3. **菜单定位**：`position: fixed` + 视口坐标。侧栏是 `overflow-y: auto` 滚动容器，绝对定位会被裁剪；越界时向左/上翻转。层级定 900（顶栏 10 / 灯箱 1000 / 纸篓 1100 / 飞纸 1200 / 确认框 1300）。
4. **重命名 = 行内编辑**：右键菜单点「重命名」或**双击标题**进入；自动聚焦 + 全选；Enter 确认 / Esc 取消 / 失焦确认。
   - **不用 `maxlength` 硬截断**（粘贴长文本会被静默吃掉）：超 30 字时输入框边框转朱砂，Enter 不提交。就地反馈，不弹 toast。
   - 空标题 = 放弃、不发请求；标题未变 = 不发请求。
   - 成功后**只改本地那一条的 title，不重拉列表**——后端刻意不刷 `updated_at`，位次本来就不动。
   - 输入框在 `v-for` 里，用**函数式 ref**（字符串 ref 会被 Vue 收成数组）。
5. **标题悬停滑动**：与书架题签同一套做法——本地指令量 `scrollWidth - clientWidth`，只有真溢出的行加 `--overflow` 类，把位移/时长写进 CSS 变量，悬停时 50px/s 匀速滑到末尾再回位。右缘 10px 渐隐提示「后面还有字」。
6. **发送中禁止改元数据**：`sending` 时右键不开菜单、双击不进编辑（避免删除/重命名让当前流落到不存在的会话上）。

### 怎么验的

Playwright 探针（真后端 8000 + 真库，`--no-proxy-server`），**24 项全绿**：

- 旧的两个图标已删除；行内只剩 `session-open` 一个子元素
- **悬停不出现任何按钮**（只允许右键）
- 右键弹出 3 项、顺序 `重命名 / 导出 Markdown / 删除`、有分隔线、菜单在视口内
- Esc 关菜单、点外部关菜单
- 双击进入编辑、预填原标题、**自动全选**（selectionStart 0 / selectionEnd = len）
- Esc 退出且标题未变
- 超长（32 字）→ 边框转朱砂、Enter 不提交
- 合法重命名 → 标题更新、**id 序列不变**（证明位次不动）、刷新后仍在（已落库）
- 探针收尾**改回原标题**，不留痕
- 菜单「删除」→ 朱砂确认框，取消后会话仍在
- 菜单「导出」→ 真发起 `/api/qa/sessions/{id}/export`
- 45 行里 30 行溢出 → 30 行被标可滑动，`--title-shift: -90px` / `--title-scroll-dur: 6s`，悬停触发 `session-title-scroll` 动画
- 零 JS 报错；`vue-tsc --noEmit` 0 错误

### 途中抓到的真 bug（已修 + 已固化为断言）

插入重命名错误提示时，段落落在了 `<ul v-if>` 与 `<p v-else>` **之间**，把两者拆成两个独立条件 → **列表与「没有匹配的会话」同时渲染**。修法：错误提示移到 `v-if/v-else` 整组之后。探针加了「列表与空态互斥」断言防回归。

### ⚠️ 部署前必须补（跨目录，不在本批授权内）

`backend/app/main.py:70` 的 CORS `allow_methods=["GET","POST","PUT","DELETE"]` **没有 PATCH**。开发态走 vite 代理是同源，看不出来；前后端分域部署时重命名会被预检拒绝。同时 `tests/api/test_cors.py:49` 有一条测试断言「PATCH 无路由使用、不放行」，语义已过时（v36 起有 PATCH 路由）。**需派后端会话改这两处。**

### 已知限制

- 右键会吃掉整行的浏览器原生菜单（行内没有需要「在新标签打开」的链接，可接受）
- 行内编辑时侧栏只有 248px，长标题看不全（通常改的是前缀）
- 触屏无右键：目前**没有**兜底入口（主人拍板只允许右键）。若将来要在触屏用，需要另加长按识别。

## 2026-11-25 · 纸篓改后端（软删除）+ localStorage 方案退役 · 执行者:ZCode

**为什么做**：承接后端 v37（`notes.status='archived'` 软删除 + 30 天清理）。旧前端纸篓把「哪些笔记在篓里」存在 `localStorage['baleen-trash-ids']`——换浏览器即空、不同账号共用一个 key 会互相清空，且检索仍能命中纸篓里的笔记（因为后端一行没动）。

### 产物

| 文件 | 改动 |
|---|---|
| `src/api/notes.ts` | `deleteNote` 注释改为「移入纸篓」；新增 `TrashItem`/`TrashPage`/`listTrash`/`restoreNote`/`purgeNote` |
| `src/stores/trash.ts` | **重写**：只保留纯视觉的 `flying`（飞纸动画）+ `count`（角标数量缓存，`refreshCount()` 从后端同步）。不再存 id 清单、不碰 localStorage |
| `src/components/BasketModal.vue` | 改查 `GET /notes/trash`；每条显示「剩 N 天」（3 天内转朱砂，0 天说「今天到期」）；放回/放回全部/彻底删除都走真 API；提示文案改为「纸篓对当前账号生效（换浏览器也在）；移入满 30 天后自动彻底删除」 |
| `src/views/LibraryView.vue` | `visibleNotes` 不再本地过滤（服务端列表本来就只返回 active）；批量删除改为逐篇 `deleteNote` + `Promise.allSettled`，失败如实报数；`onActivated` 同步纸篓数量 |
| `src/App.vue` | 拖书入篓改为调 `deleteNote`；角标用 `trash.count`；新增 `basketError` 提示（卡片飞走了但后端没记账时必须让人知道）；挂载/切页/关弹窗三处同步 count |
| `src/views/QaView.vue` | 删掉「篓」引用标——v37 后检索过滤 `status='active'`，纸篓里的笔记不可能被命中，该标记已无意义 |

### 关键决策

1. **store 语义收窄**：从「纸篓数据源」变成「纯视觉状态 + 数量缓存」。清单归后端（`BasketModal` 自己拉），避免两处真源。
2. **书架不再本地过滤**：后端 `/api/notes` 只返回 `active`，本地再滤一遍是重复真源（v37 前是必要的，现在不是）。
3. **飞纸先飞完再发请求**：并行会让「动画飞完但服务端失败」不可见。失败时 `basketError` 明确告知（卡片已从书架消失但后端没记账，刷新会回来）。
4. **「篓」标下线**：不是偷懒——它存在的理由是「篓里的笔记还能被检索命中」，而这个前提在 v37 被消除了。

### 怎么验的

- **UI 端到端 10 项全绿**（真后端+真库）：`GET /notes/trash` 可用、`retention_days=30`、书架与纸篓**无交集**、弹窗提示改为账号级口径、旧文案「只记在本浏览器」已消失、每条显示剩余天数、`localStorage` 纸篓键已不存在、零 JS 报错
- **真实写路径 15 项全绿**（造临时笔记走完整生命周期：软删→书架隐藏→详情 404→纸篓含它+剩 30 天→放回→书架恢复+archived_at 清空→再软删→purge→物理消失；探针收尾已删净）
- `pnpm exec vue-tsc --noEmit` → 0 错误；后端 708 passed

### 已知限制

- 纸篓弹窗一次最多拉 100 条（`listTrash(100)`），超出的分页未做——当前纸篓规模远小于这个数。
- 「放回全部」是逐篇串行请求，几十篇时会慢几秒；没有批量端点（后端未提供）。
- 拖书入篓失败时卡片已从书架消失（乐观视觉），`basketError` 告知后需手动刷新才能看到它回来。

## 2026-11-25 · 书架固定 6 列 × 5 行（30 本刚好排满） · 执行者:ZCode

**为什么做**：主人反馈「书本刚好装下五行六列的大小刚刚好」。原实现是 `repeat(auto-fill, minmax(150px, 1fr))`——能塞几列塞几列，1440 舞台铺成 **7 列**，30 本变成「4 满行 + 2 本残行」，末行只有两本书很难看，格子还被压扁。

**产物**（`src/views/LibraryView.vue` 单文件，纯样式）：

```css
grid-template-columns: repeat(6, minmax(0, 1fr));   /* 原:repeat(auto-fill, minmax(150px, 1fr)) */
```

**效果（1440×900 实测）**：

| 项 | 改前 | 改后 |
|---|---|---|
| 列数 | 7（auto-fill） | **6（固定）** |
| 每行本数 | 7,7,7,7,2（末行残 2 本） | **6,6,6,6,6（全满）** |
| 行数 | 5 | 5 |
| 书封尺寸 | 172×241 | **204×285** |
| 宽高比 | 0.714（5:7） | 0.714（5:7，不变） |

`PAGE_SIZE` 本来就是 30（=6×5），所以分页与网格现在严格对齐：每页 30 本、每行 6 本、每行都排满。

**怎么验的**：Playwright 实测 30 本书的 `getBoundingClientRect().top` 分布 → `perRow: [6,6,6,6,5]`，行数 5，列数 6，书封比例 0.714（5:7 未被破坏）。另跑纸篓 10 项 + 会话菜单探针，均全绿无回归；`vue-tsc --noEmit` 0 错误。

**已知限制**：书封 285px 高，5 行共约 1500px，**一屏（900px）看不全，仍需滚动**。若要做到「一屏装下 30 本」，书宽需压到约 82px——标题基本读不了，故未采用。

## 2026-11-25 · 去重批（全局铁律落地）+ 书架每页 2 行 · 执行者:ZCode

**为什么做**：主人立全局铁律——「最讨厌网页上和后端有重复的内容和功能；用户一眼就知道怎么使用的功能还加一段介绍描述。后面不管做什么都不要重叠功能和内容。」本批按此自查全站，逐条裁决落地；另按主人新要求把书架改成「一页两行」。

### 一、书架：每页 12 本（6 列 × 2 行）

- `PAGE_SIZE` 30 → **12**，配合已固定的 6 列 → 正好 2 行、每行 6 本排满；
- 实测一屏（1440×900）**完全放下、零滚动**；总共 61 本 → 6 页。
- 上一批的 5 行（1500px）需要滚动，本批按主人新要求收到 2 行。

### 二、去重清单执行结果

| 编号 | 项 | 裁决 | 执行 |
|---|---|---|---|
| **A1** | 斜杠 `/导出` vs 右键菜单「导出 Markdown」 | **删斜杠**（右键能导出任意会话，作用域更宽，是唯一入口） | 已删 |
| **A2** | 斜杠 `/新对话` vs 侧栏「+ 新对话」按钮 | **删斜杠**（按钮显式、一眼懂） | 已删 |
| **A3** | 斜杠 `/存为笔记` | **保留**——无其他入口，不构成重复 | 保留 |
| **A4** | 斜杠 `/检索` | **保留**——同上 | 保留 |
| **A5** | 📎 按钮 vs 拖放附件 | 不重复（同一动作的两种触发，业界通行） | 不动 |
| **B1** | 个人中心章程预览（前 300 字） | **删**——章程真源在 /charter | 已删 |
| **B2** | 个人中心模型配置只读清单 | **删**——设置页是唯一可编辑真源 | 已删 |
| **C2** | 视频页空态三步流程 | **删**——placeholder 已说明怎么用 | 已删 |
| **C5** | CharterWizard 页头长段描述 | **删**（d13e365 挂账项就此闭环） | 已删 |

斜杠命令从 4 条收敛到 2 条，且**留下的两条都是唯一入口**；删掉的两条各自有更合适的显式入口。

### 三、怎么验的

`verify_dedup.py`（真后端+真库），**19 项全绿**：

- 书架：12 本、6 列、2 行、`perRow=[6,6]`、**页面溢出 0px**
- 斜杠命令 = `['/存为笔记', '/检索']`；`/新对话`、`/导出` 均不在
- **右键菜单仍能导出**（确认删的是重复项而不是唯一入口）
- 个人中心：`.charter-preview` 0 个、`.config-row` 0 个，账号身份与两个入口仍在
- 视频页：`.tray-steps` 0 个、无「成一本书」
- 章程页：`.page-desc` 0 个、无「唯一需要完成的一步」
- 零 JS 报错；`vue-tsc --noEmit` 0 错误

### 已知限制 / 一个需要主人决策的点

**个人中心这一页现在只剩「头像 + 两行账号信息 + 两个跳转按钮」，已经没有任何独立信息了。** 按铁律的严格推演，这一页本身可能就该合并掉（账号信息可进顶栏用户菜单，「章程/配置」在各自页面已有入口）。本批**未擅自删页**——删页面属于导航结构变更，等主人裁决。

### 顺带记录

- 「使用统计」（笔记/来源/会话/消息）在更早一批已按主人意见删除，本批探针一度误断言它存在，已修正断言（非代码问题）。

### 追加：个人中心「模型配置」二次入口删除（主人当场指出）

**问题**：上一批把配置只读清单删了，但留了个「模型配置」跳转按钮——而侧栏书立里本来就有「设置」，这就是同一功能的第二个入口。

**处置**：删掉该按钮。跳转区只剩「知识库章程」一个。

**同时核实了「知识库章程」这个按钮不是重复入口**：grep 全站确认章程页(`/charter`)的常规入口**只有个人中心这一处**（`App.vue:153` 的章程链接只存在于引导期，引导完成后不渲染；设置页没有任何章程入口）。所以它必须留——删了就没有入口了。

| | 改前 | 改后 |
|---|---|---|
| 个人中心跳转按钮 | 知识库章程 + 模型配置 | **知识库章程** |
| 设置页入口 | 侧栏「设置」 | 侧栏「设置」（不变） |

验证：`verify_dedup.py` 追加两条断言——「模型配置」文字不出现、`.links-card .btn` 计数 == 1；全套 21 项全绿；`vue-tsc` 0 错误。

**注意**：后端 `GET /api/profile` 当时仍返回 `settings` 块（llm_base_url/model/密钥掩码），前端已不再使用。**该冗余已在同日下一批清理完毕**（api.md v38，见下方「追加」），此段仅记录当时状态。

### 追加：`api/profile.ts` 类型收缩（配后端 v38）

后端 `GET /api/profile` 从四块聚合收缩到两项（`user` + `charter.has_charter`），删掉 `settings` / `stats` / `charter.preview` 三个前端已不消费的死块。

- 删 `ProfileStats`、`ProfileSettings` 两个 interface；`ProfileCharter` 只留 `has_charter`（删 `preview`）。
- `ProfileView.vue` **无需改动**——它本来就只用 `user.{id,username,created_at}` 与 `charter.has_charter`（grep 验证过），这也是判定那三块是死数据的依据。
- 文件头注释写明去向：配置 → `settings.ts`，完整章程 → `charter.ts`。

验证：`vue-tsc --noEmit` 0 错误；浏览器实测个人中心正常渲染、零 JS 报错。

### 追加：个人中心整页下线（api.md v39，主人拍板「先把个人中心裁了」）

v38 收缩后该页只剩头像条 + 章程按钮，已无独有信息（UI设计总监按铁律推演的余波，主人拍板执行）。

- 删 `/profile` 路由与 ProfileView 视图、书立「我的」按钮、NoteView 的 `case 'profile'` 返回文案；`api/profile.ts` 整文件删除。
- 工作台顶栏「个人中心」链接改为「**知识库章程**」——`/charter` 在工作台态的唯一入口原本就在个人中心里，入口平移顶栏（引导态顶栏本有此链接，两形态同款）。
- 账号身份顶栏常驻用户名，不丢信息；注册时间无展示位不再提供（死信息）；`/profile` 旧 URL 走 catch-all 回 `/qa`。

验证：`vue-tsc --noEmit` 0 错误；Playwright 8 项全过（顶栏有章程无个人中心/用户名在/书立无「我的」/章程可入/`/profile` 重定向回 `/qa`/零 pageerror）。

## 2026-11-26 · 书架翻页钉在页底（主人截图反馈）

**问题**：最后一页只有 1 本书时，书架高度缩到一行，翻页跟着内容上浮到页面中部（截图可见，第 6/6 页翻页漂在 475px 处）。

**根因**：翻页是「紧跟在内容后面」的流式布局（`margin-top: 26px`），内容多高它就落在多高之后。

**修法**：给书架页单独一个 `stage--fill` 高度形态，让翻页用 `margin-top: auto` 钉在页底。

| 文件 | 改动 |
|---|---|
| `src/App.vue` | 新增 `.stage--fill` / `.stage-inner--fill`（`notes` 路由生效）：舞台占满可视高度（`calc(100vh - var(--chrome-h))`），保留内边距（书架是内容页，不该像问答页那样出血） |
| `src/views/LibraryView.vue` | `<section>` 加 `.library-page`（`display:flex; flex:1; min-height:0; flex-direction:column`）；书架/列表/空态 `flex: 1 0 auto` 撑开剩余空间；`.pager` 的 `margin-top: 26px` → `margin-top: auto; padding-top: 26px` |

**踩过的坑（记录）**：第一版用 `.library-page { min-height: 100% }`，实测无效——`.stage` 有 `padding: 26px 30px 56px`（上下共 82px），百分比高度按**父内容盒**算，子页只有 756px 而非可视高度 847px，`margin-top:auto` 没有多余空间可用，末页翻页仍漂在 475px。改成「父级 `stage--fill` 给确定高度 + 子页 `flex:1`」才对。

**怎么验的**：Playwright 实测首页与末页的翻页 `getBoundingClientRect().bottom` —— **首页 844px、末页 844px、位移 0px**（修前漂移 334px）；两页 `pageScroll` 均为 0。已把「末页翻页钉在页底（位移 ≤2px）」写成 `verify_dedup.py` 的常驻断言，全套 22 项全绿。`vue-tsc` 0 错误。

### 追修：翻页位置仍随窗口高度漂移（主人二次反馈）

**主人指出的现象**：「最后一页位置上移了，但前面按钮又有点靠下，可能还是笔记本占位太大」——**判断完全正确**。

**实测证据**（修前）：

| 窗口高 | 第 1 页 pagerTop | 末页 pagerTop | 第 1 页整页滚动 |
|---|---|---|---|
| 900 | 793 | 793 | 0 |
| 860 | 784 | 753 | 0 |
| 820 | 784 | **713** | **15px** |
| 780 | 784 | **673** | **55px** |
| 740 | 784 | **633** | **95px** |

**根因**：2 行书约 659px，而可用高度 ≈ 648px（900 − 顶栏 53 − 舞台内边距 82 − 搜索栏 66 − 翻页 51）——**只差一点，窗口稍矮就溢出**。一旦整页滚动，翻页被推到内容之后：第 1 页要滚 95px 才看见翻页，末页只有 2 本反而高高在上，同一控件在不同页相差 150px+。

**修法**：让书架区域**自己滚动**，整页不滚。搜索栏 / 书架视口 / 翻页三者位置恒定。

```css
.library-page > .shelf,
.library-page > .shelf-rows {
  flex: 1;
  min-height: 0;
  overflow-y: auto;          /* 书架内部滚,不推整页 */
  scrollbar-gutter: stable;  /* 滚动条出现/消失时格子宽度不跳 */
}
```

**验证**（六个窗口高度 900/860/820/780/740/700）：首页与末页的 `pagerBot` **完全一致、位移 0px**，`pageScroll` 全部为 0。已把「末页翻页不上浮（≤2px）」与「书架页整页零滚动」写成 `verify_dedup.py` 常驻断言，全套 24 项全绿；`vue-tsc` 0 错误。

**取舍说明**：书架内部滚动意味着矮窗口下需要滚动才能看到第 2 行书（内容确实装不下），但翻页永远在固定位置——这是「控件位置恒定」优先于「一屏看全」的选择。若希望一屏看全 12 本，得把书封压到约 150×210，标题会明显变小。

### 追修：书封改小 + 间距加大（主人「改小并适当增加间距，整体更简约大气」）

**改前**：书封 201×282、列间距 16px/行间距 18px、`1fr` 撑满 → 书贴满整行、格子间挤，两行就顶到边。

**改后**：

| 项 | 改前 | 改后 |
|---|---|---|
| 书封 | 201×282 | **168×235**（5:7 比例不变） |
| 列间距 | 16px | **28px** |
| 行间距 | 18px | **24px** |
| 列宽策略 | `1fr`（随窗口变胖） | **`minmax(0, 168px)` + 居中**（定宽，两侧自然留白 70px） |
| 垂直对齐 | 顶部堆叠 | **`align-content: center`**（上下留白各 52px 对称） |

**为什么定宽而不是 1fr**：`1fr` 会让书随窗口宽度变胖，间距相对变窄——「简约大气」靠的是**书不撑满、留白呼吸**，不是把格子塞满。定宽后两侧各留 70px，视觉重心居中。

**顺带解决的老问题**：改前两行共 660px、可用 659px（差 1px 就溢出）；改后内容 555px，900px 窗口下书架**不需要滚动**（矮窗口仍内部滚，翻页位置恒定不变）。

**怎么验的**：实测 `cover 168×235（ratio 0.714）`、`gap 24/28`、两侧留白 70px、上下留白各 52px、`pageScroll=0`、`shelfScrolls=false`；末页 `pagerBot=844` 与首页一致。

**探针同步更新**：`verify_dedup.py` 的 D3 段原断言「个人中心有 who-card / 两个跳转按钮」——该页已由另一会话按主人裁决整页下线（api.md v39），断言改为「访问 /profile 被兜底重定向 / 无 who-card / 章程入口已平移到顶栏 / 无模型配置二次入口」。全套 24 项全绿。

### 追修：矮视口下第二行被裁（主人「第二行还是显示的不完整」）

**真 bug，已复现**。用视口高度梯度实测：

| 视口高 | 修前状态 | 说明 |
|---|---|---|
| 900 / 860 / 820 | OK | 内容装得下 |
| 780 | **上裁 + 下裁** | row1Top 138 > shelfTop 134、row2Bottom 675 > 661 |
| 768（笔记本常见） | **上裁 14px + 下裁 29px** | `scrollTop` 恒为 0，**滚不回来** |
| 720 / 660 | 裁切加剧 | 溢出 77 / 137px |

**两个叠加的根因**：

1. **`align-content: center` + `overflow: auto` 的经典陷阱**：内容超高时，居中会让上下**同时**溢出，而 overflow 的滚动起点仍在内容顶部 → 顶部区域永远滚不到。修法用 `align-content: safe center`（溢出时自动退化为 start）。
2. **内容本身装不下**：`safe center` 只解决了"能滚到"的问题，但 768 屏下 2 行书 555px 塞不进 539px 可用高度，"一页两行"的承诺就破了，只能滚动——那就失去意义了。

**最终方案：按视口高度自适应收书封**（`max-height` 媒体查询，因为问题是纵向装不下）：

| 视口高 | 书封 | 行/列间距 |
|---|---|---|
| > 800 | 168×235 | 24 / 28 |
| ≤ 800 | 148×207 | 20 / 24 |
| ≤ 740 | 128×179 | 16 / 20 |
| ≤ 680 | 112×157 | 14 / 16（标题字号同步降到 11px） |

**验证**：八个视口高度（900/860/800/780/768/740/700/660）**全部 OK** —— 上下都不裁切、`innerScroll = 0`、`pageScroll = 0`，且"一屏两行"始终成立。已把「900/768/700 三个高度下两行都不裁切」写成常驻断言。

**为什么用 `max-height` 而不是 `max-width`**：要解决的是纵向装不下，不是横向。
