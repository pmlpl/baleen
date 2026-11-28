# 黑话词典（glossary）

> 新会话/新读者遇到看不懂的词先查这里；一词一行，真源在外面的按「→」跳。
> 收词标准：本仓库文档/日志/交付块里高频出现、但对新读者不自解释的词。
> 2026-11-26 创建（WorkBuddy 复评报告 C6）。

## 导入流水线

| 词 | 意思 |
|---|---|
| **G0** | 解析层：文件（md/html/pdf/docx/csv/图片/视频…）→ 标准块结构 `parse/` |
| **G1 价值闸** | 启发式预筛「这文件有没有资格当笔记」：空壳/测试文件/壁纸截图直接拒 → `gate/g1.py` |
| **G2** | 「这条内容归不归属本库」的判断位（历史：LLM 判官 → v21 起改 organizer 编辑，ADR 0008） |
| **organizer** | LLM 读笔记契约（charter + note-format）重写 md：补标题/去噪/组织结构 → `rewrite/organizer.py` |
| **charter（章程）** | 用户个人的知识库契约：问卷答案 → LLM 生成 → 存 `app_settings.charter`，导入整理时注入；v40 起问卷只问事实（昵称/年龄段/性别/学历/状态/为什么用），偏好由 LLM 推导 → `api.md` §8 |
| **父块 / 子块** | 切块两级：检索用**子块**（向量细粒度），回答喂**父块**（上下文完整）——即 small-to-big，ADR 0004 |
| **去重（dedupe）** | 同 `(owner_id, sha256)` 的导入直接复用旧结果；视频删后重导走三分类判据（有 active=真去重 / 只剩 archived=重跑 / 全无=复用旧结论） |
| **parse_status** | source 的解析状态机：pending → ok / error / g2_skipped 等（CHECK 约束是真源） |
| **video_log** | 视频导入的白盒进度日志：`parse_meta.video_log = [{ts, stage, pct}]` 数组，前端全程可见；**worker 阶段往 parse_meta 写新键一律 `video_` 前缀**（G0 落盘自动保留，前缀外例外键见 `pipeline._VIDEO_KEEP_EXTRA`） |
| **SESSDATA** | B 站登录态 cookie：只在被 412 风控时当 Playwright 浏览器兜底的门票（HTTP 重放无效，TLS 指纹打分） |
| **纸篓 / archived** | 删除=软删（`status='archived'`+`archived_at`，检索不再命中）；纸篓 30 天 arq cron 自动彻底删；`purge` 只认 archived（防绕过软删毁 active） |

## 检索与问答

| 词 | 意思 |
|---|---|
| **HNSW / trgm** | 向量近似索引 / 文本三元组模糊索引——两条检索路，RRF 融合 |
| **RRF** | Reciprocal Rank Fusion：两路排名倒数融合，本项目 rerank 即此（不引 cross-encoder） |
| **agent loop / 决策树** | 问答两级：L0 确定性决策树（预检→库内→兜底）；L1 agent（LLM 选工具：`search_kb` 换词再搜 / `search_web` 转联网 / 直接答），预算三道闸在代码——「机制与策略分离」是答辩口径，ADR 0010 |
| **[NO_KB]** | 库内答不了的标记行：AI 换行后用联网结果或自身知识继续答（不拒答、防瞎编） |
| **[n] / [引n] / [W n]** | 答案引用标注：`[n]`=检索片段、`[引n]`=用户手动引用的笔记、`[W n]`=联网来源 |
| **digest** | 长会话的冻结分段摘要：第二条不可变 system，保前缀缓存、止静默丢失 → `qa/digest.py` |
| **昵称** | 问卷第一题，存 `app_settings.nickname`，问答 system prompt 注入（AI 用它称呼用户；没填不注入） |
| **缓存命中率** | Σhit/Σ(hit+miss) token 加权；分 overall/库内轮/联网轮三桶——联网轮现抓内容结构性不可缓存，偏低不是 bug |
| **demo 围栏** | ```demo 理科交互演示：模型输出完整自包含 HTML（零外部资源）→ 沙箱 iframe 可交互；**完整文档型 ```html 也自动提升**，片段型保持代码块 → `utils/demo.ts` |
| **沙箱配方** | demo 的安全边界：`sandbox="allow-scripts"` 刻意不给 same-origin（不透明源，parent/localStorage 全抛）+ srcdoc 注入 CSP `default-src 'none'`（网络全禁）+ postMessage 单向高度通道（伪造源被拒）；与 SVG 沙箱（纯静态零脚本）配套成两档 |

## 工程与协作

| 词 | 意思 |
|---|---|
| **画像页** | 章程页扩成的个人页（api.md v41+）：左栏五项事实可原地编辑，右栏八块真实统计（互斥三分口径）+ 主题打标写 `notes.tags`；「我答过的问题」清单带真实 total → `api.md` 画像节 |
| **基线** | 全量 pytest 通过数（当前 **743 passed +1 xfailed**）：唯一真源是 STATUS 里 `基线 **N passed**` 加粗声明；动了测试必须同步升它和 CI 的 grep 行——只增不降 |
| **doc_lint** | `scripts/doc_lint.py`：文档一致性护栏（端点数/基线声明/链接/台账同步…），pre-commit 拦 ERROR；⚠️ 它只对账声明之间的一致性，不验真实数——协调方终验要独立复跑 |
| **交付块** | 每批工作的正式记录（产物表/怎么验的/已知限制），归 `backend/delivery/<月>.md` / `frontend/DELIVERY.md`，格式见 HANDOFF §4——没写交付块的交付不算交付 |
| **ADR** | 架构决策记录 `docs/adr/00XX-*.md`：为什么这么定 + 不这么定的理由，答辩素材 |
| **台账 / 挂账** | 台账=持续登记的结构化记录（缺陷台账 `eval/analysis/defects_ledger.*`）；挂账=已识别未拍板的待办（列 STATUS 未决/日志悬着节） |
| **断点续跑记录** | `eval/runs/quality_scan_llm_state.json`：质检 LLM 终审做过的笔记 id 列表，重跑跳过不重复烧 token |
| **协调方** | 跨会话的整合角色：STATUS 速览维护者、终验独立复跑、并发工作区提交仲裁（见 HANDOFF §0.3） |
| **终验** | 协调方对任一会话交付的独立复核：全量 pytest + doc_lint + build + 探针——工作区绿 ≠ 提交树绿，必须独立跑 |
