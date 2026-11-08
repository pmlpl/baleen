# HANDOFF — 线间交接台

> **单入口，各自出口。** 所有 agent **只读**本文件；交付写在你自己目录下的 `DELIVERY.md`。
> 这样不会出现两个 agent 同时写一个文件、互相覆盖的问题。

---

## 0. 谁读、谁写（先看这张表）

| 文件 | 谁写 | 谁读 |
|---|---|---|
| `HANDOFF.md`（本文件） | **只有人类 / 协调方** | **所有 agent 都读** |
| `STATUS.md`（顶部速览） | **只有协调方** | 人类读 |
| `backend/delivery/*.md`（交付块） | 只由**后端线**追加 | 人类读 |
| `frontend/DELIVERY.md` | 只由**前端线**追加 | 人类读 |
| `eval/DELIVERY.md` | 只由**评测线**追加 | 人类读 |
| `docs/adr/*.md` | 对应任务的 agent | 所有人 |
| `AGENTS.md` | **任何人**（改动必须留痕：§11 记录 + 回复明示，见其 §3.11） | 所有 agent 读 |

**铁律**：agent **不许编辑 `HANDOFF.md`**。要回报就写进自己目录的交付文件。

---

## 0.1 会话登记（**角色制**：一个岗位可以有多个 agent 先后担任）

| 角色 | 管哪些目录 | 当前担任 | 历史担任 |
|---|---|---|---|
| **后端** | `backend/` | **DSH-后端**（线 M：查询 API） | `ZCode-后端`（线 A 骨架 / B 解析 / D 数据模型 / E 切块向量化 / F .env / G G1 / K 检索）· `Trae-后端`（线 H-批1 / H-批2 / I-批1 / J） |
| **评测** | `eval/` | **DSH-评测**（线 N：20 query 集 + 切块消融） | `Trae-评测`（线 C 全阶段 + `stratified-v2`） |
| **前端** | `frontend/` | **DSH-前端**（线 L：三页界面） | — |

**核心原则：任务按目录划分，不按 agent 划分。**
后端这个岗位换人是正常的，唯一要求是**换人时必须走 §0.2 的交接协议**——不允许"新人直接上"。

### 会话命名（主人用）

```
{执行者}-{角色}       例：ZCode-后端、Trae-后端、Trae-评测
```

同一个会话可以用同一岗位名持续接活；**换会话就起新名字**（`ZCode-后端` → `Trae-后端`），
这样从名字就能看出"谁在什么时候接的"，历史记录不会混。

---

## 0.2 交接协议（任何人接手任何角色都必须走）

### ★ 接手者开工前必做：写「接手确认单」

**在写第一行代码之前**，把下面五项写进回复或 `DELIVERY.md` 顶部，等人确认：

```
【接手确认单 · {执行者}-{角色} · 接手 {线 X}】

1. 当前状态我的理解：
   - 已完成：____（列模块）
   - 测试基线：____ passed
   - 还没做：____

2. 我的任务（用自己的话复述一遍 HANDOFF §2 给我的那节）：
   ____

3. 我打算创建/修改的文件（具体路径）：
   ____

4. 我知道不能碰的：
   eval/samples/ · eval/labels.csv · HANDOFF.md · 其他角色的目录
   （`AGENTS.md` 可改，但改动必须留痕：§11 记录 + 回复明示）

5. 我拿不准的（逐条列，没有就写"无"）：
   ____
```

**人类/协调方逐条确认后，才开始动手。**

> **为什么非要这一步**：信息差不会在交付时暴露，只会在**做错方向之后**暴露。
> 让接手者先用自己的话复述一遍，跑偏在 0 行代码时就能拦住，代价最小。

### 交接信息的必读顺序

| 顺序 | 文件 | 读什么 | 解决什么 |
|---|---|---|---|
| **1** | `STATUS.md`（速览）+ `backend/delivery/`（交付块，或对应目录的 `DELIVERY.md`） | 顶部「系统状态速览」+ 全部交付块 | 已经有什么、踩过哪些坑、什么还没做 |
| **2** | `HANDOFF.md` | §0 / §0.1 / **§2 你的那节** / §3 / §3.1 / §4 | 现在轮到谁干什么、什么必须问 |
| **3** | `AGENTS.md` | §3 硬约束、§4 技术栈、§5 决策、§9 规范 | 规矩（不许做什么） |
| **4** | `docs/adr/` | 只读与本次任务相关的 | 为什么这么设计（**防止推翻已定决策**） |
| **5** | 代码 | 你打算改的那部分 | 现状 |

### 防信息差的四条硬规矩

1. **不许推翻 ADR 里的决策** —— 觉得该换，先写新 ADR 提案，等人确认（`AGENTS.md` §3.8）
2. **不许删或改写「已知限制」** —— 那是前人的血泪，删了等于让下一个人重踩
3. **交付时必须写「断点在哪」** —— 做到哪一步、下一步从哪接；不写清楚就是给人留坑
4. **接手者必须复述** —— 见上面的确认单

### 交接出错的三种典型症状（出现就停下问）

| 症状 | 说明 |
|---|---|
| 你在**重新发现**前人已经写进「已知限制」的东西 | 说明你没读 DELIVERY，回去读 |
| 你想**改掉**某个看起来奇怪的设计 | 先查 ADR；查不到就问，别直接改 |
| 你不知道**上一个人做到哪** | 说明交付记录不全，先问清楚再动手 |

---
## 0.3 并行时的文件归属（撞车检查表）

多条线同时在同一仓库工作时，**按路径划领地**。动手前、提交前都该核这张表。

| 线 | 领地路径 | 干什么 |
|---|---|---|
| **线 J** | `app/service/gate/g2.py` · `app/service/llm/client.py` · `scripts/g2_smoke.py` · `tests/service/gate/test_g2.py` · `tests/service/llm/test_client.py` | G2 prompt + 429 重试 + 节流 |
| **线 I-批2** | `app/worker/` · `app/api/` · `app/main.py` · `backend/pyproject.toml` | arq worker + 上传 API |
| **线 L** | `frontend/` 全目录（含 `frontend/DELIVERY.md`） | 三页界面（导入 / 笔记库 / 检索） |
| **线 M** | `app/api/library.py`（新）· `app/repo/library.py`（新）· `app/main.py`（只加 2 行）· `tests/api/`（新）· `STATUS.md` | 4 个查询 API（`docs/api.md` §3 §4） |
| **线 N** | `eval/queries/`（新）· `eval/scripts/ablation.py`（新）· `eval/ablation.md`（新）· `eval/scripts/make_split.py` · `eval/metrics.py` · `eval/split_meta.json` · **`eval/labels.csv` 的 `split` 列（主人 2026-10-29 专门批准，仅此一列）** · `eval/DELIVERY.md` | 20 query 集 + 切块消融 + 槽位细化到 (槽位,判定) |
| **协调方** | `HANDOFF.md` · `charter.md` · `docs/adr/` · **`docs/api.md`** · `eval/rubric.md` · `eval/labels.csv`（除 `split` 列） · `AGENTS.md` · git 提交 | 只由协调方改 |
| **已冻结** | `app/service/parse/` · `chunk/` · `embed/` · `imports/pipeline.py` · `app/repo/`（已有文件）· `alembic/` · `eval/samples/` | 已验收，要改先提 ADR |

### 三秒自查

```powershell
cd baleen
git status --short      # 谁在动，按上表认领
```

**判断撞车的唯一标准**：同一个文件同时出现在**两条线的领地**里。
出现就停下问协调方，**不要"我改我的"**。

### 交付记录也分人

两线并行时，`backend/delivery/` 每个块**标题必须写明执行者与线号**：

```
## 2026-10-28 · 线 J · G2 政策修正 · 执行者：Trae-后端
## 2026-10-28 · 线 I-批2 · arq worker + API · 执行者：ZCode-后端
```

写文件时报「文件已变更」→ **重读一遍再追加，绝不覆盖别人的块**。

### agent 在并行期间不许做的

- ❌ `git add` / `commit` / `checkout` / `clean` / `stash`（提交只由协调方做）
- ❌ 清理或还原工作区（会毁掉别人未提交的改动）
- ❌ 动「已冻结」和「协调方」的路径

---
## 1. 当前状态

**所有已派的线都已验收，当前没有进行中的线。**（2026-11-07 核实）

### 已验收的线（A–Q）

线 A 环境骨架 · B 解析 + G0 · D 数据模型 · E / E-2 切块与向量化 · F `.env` 加载 ·
G G1 价值闸 · H-批1 LLM adapter · H-批2 G2 归属闸 · I-批1 导入状态机 · I-批2 worker + 上传 API ·
J G2 政策修正 · K 混合检索 · M 查询 API · L 前端三页 · N query 集与消融 · Q 问答批1-8。

> **明细不在这里。** 每条线的「产物 / 自测 / 已知限制」在 `backend/delivery/` 归档里；「现在有哪些模块、基线多少」在 `STATUS.md` 顶部速览里。
> 本表只回答「轮到谁干活」，不记「第几个测试通过」——那种数字只有一个真源。

### 2026-11 起的交付（未按线编号）

视觉解析、多用户认证、charter 问卷、标题整理、笔记编辑/删除、CSV 导入、扫描件 PDF、
md 带图导入、SVG 插图、对话附件（文本+图片）、笔记渲染 v18 —— 均已完成，
名字只出现在 STATUS 速览的模块表里，明细看 `git log` 与 `../日志/2026-11-*.md`。

### 历史真问题：现在的状态

| 当初的问题 | 现在 |
|---|---|
| `notes.markdown` 全 null（真源与渲染脱节） | ✅ 死列已删（迁移 `0010`），正文由 `doc` 现场序列化 |
| 新 source 在 worker 处理前 `parse_status=error` | ✅ 加 `pending` 枚举（迁移 `0009`） |
| 2 个子块超 bge 512 token 被静默截断 | ✅ 代码块超限按行切（`code_part` 编号） |
| `vite.config.ts` proxy rewrite 从一开始就是错的 | ✅ 已修 |
| ADR 0004 §5「heading 前缀提升判别力」缺实验 | ✅ 已做 A/B：**给 text/table 裸加前缀 R@5 反掉 2 条**，不改生产（`eval/prefix_ablation.md`） |
| 20 条 query 相关性是执行者自判 | ✅ 主人已逐条复核（15 对 / 2 勉强 / 1 改问法） |
| 槽 7 分层没细到 (槽位, 判定) | ✅ test 18/18 覆盖，误拒率 0% 不再是假象 |
| 切块参数 250–400 是推导值、没做过实验 | ✅ 三档消融校准，维持 300（`eval/ablation.md` §12） |
| **检索没有相关性阈值** | ⚠️ **仍未解决**（RRF 无绝对量纲，v1 取舍见 `docs/api.md` §4） |

### 两条还在生效的教训

1. **契约/配置写完要真打一次请求** —— proxy rewrite 错了 17 天没人发现，因为只有 `/healthz` 通。
2. **合成 fixture 全绿 ≠ 真实数据可用** —— 27 个切块测试通过，真实笔记 in_range 只有 11.6%。

---

## 2. 待处理

**导入链已闭环**（上传 → 排队 → G0/G1/G2 → note → chunks → embeddings），见 §1 的 I-批1 / I-批2。当前待办见下面两条线。

### ✅ 已拍板

| 决定 | 结论 | 日期 |
|---|---|---|
| `embeddings.vec` 写入方式 | **选 A：`uv add pgvector`**，用官方 `HALFVEC` 类型替换自定义 `HalfVec(UserDefinedType)` | 2026-10-28 |
| 向量化推理栈 | **GPU 版 torch**。本机 RTX 3060 Laptop / 6 GB 显存 / 驱动 616.56。**必须做 device 自动降级**（`cuda if torch.cuda.is_available() else cpu`），因为答辩机器可能没有 GPU | 2026-10-28 |

---

### ⏭ 下一批

**2026-10-29 01:30 已派活**：线 L / 线 M / 线 N 三线并行，互不重叠目录，可同时跑。

| # | 线 | 干什么 | 状态 |
|---|---|---|---|
| 1 | **线 M** | 后端 4 个查询 API（`docs/api.md` §3 §4） | ✅ 已验收并提交 `745f47c` |
| 2 | **线 L** | 前端三页（导入 / 笔记库 / 检索） | ✅ 已验收并提交 `f80df35` |
| 3 | **线 N** | 20 query 集 + 切块消融 + 槽位细化到 (槽位,判定) | ✅ 已验收并提交（见 `git log` 线 N 一条） |
| 4 | — | **G2 live 复测** | ✅ **已完成（2026-10-31，真链路全量）**：`g2_eval.py` parse→G1→G2 跑全 35 样本（deepseek-flash，6.6 万 token），**误拒率 0/15 = 0%（ok 口径，基线 53.3%）**、非法组合 0、唯一判定错是 1 条误收；十节正式报告 `eval/report.md`（test split）/`report_g2_ok.md`（ok 口径），见 backend/delivery/ 线 R-G2 节 |
| 5 | 线 Q | **问答生成** → 已实现并升级为聊天式多轮 + SSE 流式 + 设置页热生效 | ✅ 线 Q 批1-8（见 backend/delivery/ 线 Q） |
| 6 | — | **人工复核 20 条 query 的期望** —— 工具已经备好：**`eval/queries/复核单.md`**（每条 query 下面直接抄出「我认为答案在的那一节」的真实正文，只需判断答案在不在那儿；⭐ 标记的 6 条是重点） | ✅ **已完成（2026-10-29，主人逐条判定）**：15 对 / 2 勉强（q07、q09）/ 1 不对（q04）/ 负例维持 2；q04 经主人批准改问法（monkeypatch 前提与原文不符），指标已按复核后标签重算——**300 档 R@5 = 100%**（宽松 n=18 / 严格 n=16 一致），见 `eval/ablation.md` §11 |
| 7 | — | 补 `notes.markdown` 生成 / 代码块超限 / heading 前缀 A/B（见 §1「真问题」1/5/6） | 未派，等人拍板 |
| 8 | — | **工具链 + 阶段3/4**（联网搜索 / 写笔记 / 导入后主动总结 / 会话产出笔记） | 📋 **ADR 0005 提案已出（f051998），3 个决策点待主人拍板后动工** |

**~~槽位细化的缺口~~（2026-11-02 复核：已闭环，本段作废）**：`(槽位, 判定)` 共 18 个组合
**test 已覆盖 18/18**（`eval/labels.csv` 实测，test 18 条 / dev 17 条）；当初缺的
(2,accept)/(4,reject)/(5,accept)/(7,accept) 全部就位，其中 **(7,accept) = test/`_inbox/8b8442659...jpg`**
（手写笔记，G1 判定 accept）。也就是说「误拒率在 test 上是虚的」这个担忧**已经不成立**，
无需再挪样本、无需再动 `eval/samples/`。

**派活前先确认**：`STATUS.md` 顶部速览里的模块表还准不准（现在**后端 + 前端全绿**，只差问答生成）。

---
## 3. 卡住时找谁

**遇到下面「必须停下问」的，停下来把问题写进你目录下的 `DELIVERY.md`，等人回答。不要自己试一堆方案。**

| 🔴 必须停下问主人 | 🟢 自己决定，做完记一句 |
|---|---|
| 要新增任何第三方依赖（哪怕只有 1MB） | 文件/函数的命名、目录细分 |
| 想换技术方案（会偏离 `AGENTS.md` §4/§5 或 ADR） | 测试用例怎么写、断言什么 |
| 想动 `eval/samples/` 里任何文件 | 代码风格、注释多寡 |
| 想改 `AGENTS.md`、`TASK.md`、`labels.csv` 的人工标注列 | 报错文案的中文措辞 |
| 需要开代理 / 需要外网下载（主人得手动开） | 日志级别、临时脚本放哪 |
| 评测口径的理解有分歧 | 重构内部实现但不改对外接口 |
| 发现评测集本身有问题（样本坏了、标注矛盾） | 依赖版本的小幅升级（同 major） |
| 想到"我顺手也把 X 做了" 中的 X 不在任务书里 | 性能优化（不影响正确性时） |

**几个已经拍过板、不要再问的**：

- `.doc` 不支持 → G0 报「请转成 .docx」（已定，别再讨论 LibreOffice）
- `.xlsx` 暂不支持（已定）
- 纯图片 / 扫描件 → `no_text_expected`，不做 OCR（已定）
- 模型权重不进 git，路径走 `EMBEDDING_MODEL_PATH`（已定）
- 不做登录 / 多用户 / 计费（已定，见 `AGENTS.md` §8）

---

## 3.1 临时资源清理（起过服务器就必须自查）

**背景（真实事故）**：2026-10-28 凌晨 02:23，一条临时 healthz 测试命令遗留了 uvicorn，
一直占到 **当天 19:xx（约 17 小时）**，锁住 8123 端口，还导致 **Git 无法升级**
（`bash.exe` 占着 Git 的二进制）。根因是 `kill $UVPID` 只杀了外层 wrapper。

### 规则

**① 优先用 in-process 测试，压根不起服务器**

测 HTTP 端点用 `starlette.testclient.TestClient(app)` —— 不占端口、不起进程、跑完自动结束。
不需要 uvicorn，不需要 curl，不会有孤儿。

**② 真需要起服务器时（只有测"真实网络链路"才需要）**

```powershell
# 起
$p = Start-Process -FilePath "uv" -ArgumentList "run","uvicorn","app.main:app","--port","8123" -PassThru -WindowStyle Hidden
try {
    # 轮询等就绪（别用固定 sleep）
    for ($i=0; $i -lt 30; $i++) { try { Invoke-WebRequest "http://127.0.0.1:8123/healthz" -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 300 } }
    # …测试…
} finally {
    # ★ 必须 /T 连子树：uv → uvicorn.exe → python.exe，只杀外层会留孤儿
    & taskkill /PID $p.Id /T /F 2>$null
}
```

**③ 交付前自查（把输出贴进 DELIVERY）**

```powershell
Get-NetTCPConnection -LocalPort 8123 -State Listen   # 必须为空
Get-CimInstance Win32_Process -Filter "Name='uvicorn.exe'"   # 必须为空
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'baleen' }           # 必须为空
```

**④ 任何情况下都不许 `kill` 了事** —— Windows 上必须 `taskkill /T /F`。

---
## 3.2 Git 提交规矩（2026-10-28 定稿）

### ① 提交边界：**一次可验收的交付 = 一个提交**

不按文件类型分，按**交付**分：

| 提交 | 包含什么 | 消息前缀 |
|---|---|---|
| **交付提交** | agent 写的代码 + 测试 + 配置 + **该目录的 `DELIVERY.md`** + 迁移文件 | `feat(backend):` / `fix(backend):` |
| **协调提交** | `HANDOFF.md`、`docs/adr/*`、`AGENTS.md`、`eval/TASK.md` | `docs(handoff):` / `docs(adr):` |

**`DELIVERY.md` 属于交付提交，不属于协调提交** —— 它是那次交付的说明书。

### ② 顺序：**先提交交付，再写协调文档**

```
agent 交付 → 人家验收 → 【提交交付本体】→ 再改 HANDOFF → 【提交协调记录】
```

⚠️ **反过来就会出事**：先改 HANDOFF 时，agent 的代码还躺在工作区里，
一个 `git add -A` 就混进去了。**2026-10-28 连犯三次，根因都是这个顺序。**

### ③ `git add` 的依据：**`DELIVERY.md` 的「产物」清单**

agent 在 DELIVERY.md 里已经精确列了文件路径。**照清单 add，不靠猜、不用 `-A`。**

```powershell
git status --short                    # ① 先看有什么
git add backend/app/service/xxx/ `    # ② 按产物清单逐个加（PowerShell 续行用反引号）
        backend/tests/service/xxx/ `
        STATUS.md
git diff --cached --stat              # ③ 提交前再确认一遍清单
git commit -F msg.txt                 # ④ 消息用文件传（长消息会被 shell 拆坏）
```

### ④ 谁提交：**只有协调方提交，agent 不提交**

理由：**未验收的代码不该进历史。** 验收不过要 `git revert`，历史就脏了。
边界清晰度靠「DELIVERY.md 的产物清单」保证，**不靠让 agent 自己 commit**。

### ⑤ 提交消息模板

```
<type>(<scope>): <这次交付做了什么，一句话>

线: H-批1 · LLM adapter
执行者: Trae-后端
验收: pytest 62 passed；错误分类/重试/key 不泄露均有测试

- Transport Protocol 可注入，测试不联网
- 5 类错误分类，仅网络与 5xx 重试
- api_key 不进异常文本（有断言）

已知限制:
- 同步实现，调用侧需 asyncio.to_thread
```

**目标**：`git log` 读一遍就能回答——每个交付**做了什么、谁做的、验没验过、遗留什么**。

### ⑥ 历史勘误（不重写，仅记录）

以下 3 个提交**内容正确、归类不好**，历史保留不重写，查历史时留意：

| 提交 | 消息说 | 实际内容 | 原因 |
|---|---|---|---|
| `871a304` | `docs(handoff): 新增 §3.1` | 含 4 个 gate 代码文件（394 行） | `add -A` 误收 agent 正在写的文件 |
| `b69ef77` | `feat(backend): G1 价值闸` | **只有 `DELIVERY.md`** | 代码已被上一条吞走 |
| `340c4a5` | `数据模型落地 + G0 功能验证补遗` | 两个交付合一 | 两次交付之间没提交 |

> `84c13ee` 原为 `293c8dc`，消息已 amend 补正（原仅写 docs，实含 710 行代码）。
>
> **这三条不重写历史的理由**：内容都对，只是归类不好；单人仓库无远程，
> `rebase` 收益低而风险有。记在这里，比把历史翻一遍更划算。

---
## 4. 交付写在哪

做完后，**追加**到对应目录的交付文件（2026-11-07 起后端已拆分）：

| 角色 | 写哪 |
|---|---|
| 后端 | `backend/delivery/<阶段>.md`（foundation / gates-retrieval / qa-and-eval），新建阶段文件前先问协调方 |
| 前端 | `frontend/DELIVERY.md` |
| 评测 | `eval/DELIVERY.md` |

> **`STATUS.md` 顶部速览只由协调方维护**，agent 不改进速览，只往 `backend/delivery/` 里追加块。

### 交付前必跑（2026-11-07 起）

```powershell
python scripts/doc_lint.py
```

- **ERROR 必须清零** —— `pre-commit` 已挂这个闸（`git config core.hooksPath .githooks`），不清零提交不了。
- **WARN 只有两类处理**：修掉，或在交付块里写一句「已知欠账」认账。**不许沉默略过。**
- 它查的是可机械判定的那部分一致性：契约版本号、跨文档基线数字、端点总数、速览是否比最新交付块还旧、活文档里的路径是否还存在、有提交的日期有没有日志。
- **它查不了「为什么」** —— 交付块的正文仍要人写。脚本只保证数字和引用不骗人。

格式如下：

~~~markdown
## 2026-10-28 14:30 · 线 B · G0 解析模块

### 产物
- backend/app/service/parse/{__init__,types,blocks,pdf_parser,docx_parser,md_parser,pptx_parser,router}.py
- backend/tests/service/parse/test_parse.py + fixtures/
- backend/pyproject.toml（uv add pymupdf python-docx markdown-it-py python-pptx）

### 自测
- `uv run pytest tests/service/parse -q` → 12 passed
- `uv run python scripts/parse_smoke.py --samples ../eval/samples` → 35 个文件，状态分布：ok=24 no_text_expected=4 unsupported_format=7 error=0

### 已知限制
- 加密 PDF 返回 error（message="PDF 已加密，无法解析"）
- pptx 演讲者备注未抽取

### 需要确认的问题
- （无）
~~~

**低于这个信息量的交付，人家会打回。**

---

## 5. 已验收的反馈（历史）

| 日期 | 对象 | 反馈 |
|---|---|---|
| 2026-10-28 | ZCode 线 A | 骨架合格：`pgvector/pgvector:pg17` 选对、`/healthz` 真探活、repo 层隔离、无擅自加依赖。**唯一缺口**：`config.py` 缺 `embedding_model_path` 字段（`.env.example` 有，代码没读） |
| 2026-10-28 | ZCode 线 B | ADR 0002 质量很高：有实测数字（速度差 20–40 倍、表格 22 vs 26）、挖出真坑（pdfplumber 裸文本流交错、`upright` 对斜向水印失效、PaddleOCR 的 mkldnn 崩溃）。决策与评测集冲突已处理：补 `python-pptx`、`.txt` 支持 |
| 2026-10-28 | Trae 线 C（阶段 1-2） | 纪律好：做到阶段 1 就停；发现数据不一致**没有擅自改**，主动上报 |
| 2026-10-28 | Trae 线 C（阶段 3） | `report.md` 合格：10 节结构、误拒率/误丢率置顶、非法组合单列。**但 test 集 split 未分层**（槽 6/9/11/12/13/14 全在 dev，partial 0 条）→ 已要求重做，`stratified-v2` 已修复 |
| 2026-10-28 | Trae 线 C（`stratified-v2`） | 14 个槽位在 test 各 ≥1、配对样本同 split、代码去重（1197 行）、主动列 3 条已知限制。**遗留**：槽 7 分层仍未细到 (槽位, 判定) 粒度，误拒率 0% 是假象 |
| 2026-10-28 | ZCode 线 D（数据模型） | **合格**。ADR 0003 的 6 张表全部落地、`halfvec(1024)` + HNSW + trgm GIN、CHECK 约束齐全；人家独立复跑确认 `pytest 15 passed`、DB 实建 6 表、pgvector 0.8.7。**加分**：主动补上了上次遗留的 G0 功能验证（`pytest tests/service/parse` 15 passed；smoke 35 文件 → ok=21 / no_text_expected=9 / unsupported_format=5 / error=0）。**遗留决策**：`embeddings.vec` 的写入需要 pgvector 包或补 bind processor，见 §2 |
| 2026-10-28 | ZCode 线 E（切块+向量化） | **代码合格但设计有漏**。27 passed、`over_limit=0`/`truncated=0`、`HalfVec`→`HALFVEC` 已换、torch 2.14.1+cu126 上 cuda 52 items/s。**但真实笔记实测碎片化**：闭包.md 96 块/中位 79 token/`in_range` 11.6%。根因是 **ADR 0004 漏写"切完要合并"**（不是实现错误），§4.7 已补，修复任务见 §2 线 E-2。**教训：27 个测试全用合成 fixture，真实样本一个没进测试** |
| 2026-10-28 | ZCode 线 E-2（切块碎片化） | **修复到位**。真实笔记复跑：闭包.md 96块/79tok/11.6% → **50块/191tok/64.4%**；缺陷反馈 0% → **100%**（口径修正生效）；实训报告 66.7% → 80%；`over_limit=0`/`truncated=0` 仍成立；29 passed。顺带把 594 行写死的 `480` 改成 `HARD_TOKEN_LIMIT` |
| 2026-10-28 | ZCode 线 F（`.env` 加载） | **一次修对**。`config.py` 改 `BASE_DIR=parents[2]` / `ROOT_DIR=parent`，`env_file=(ROOT_DIR/".env", BASE_DIR/".env")`。人家实测：从 `backend/` 与从仓库根两种 cwd 都能读到模型路径且文件存在；31 passed（新增 2 条配置测试，用 `tmp_path` 不碰真 `.env`）|
| 2026-10-28 | ZCode 线 G（G1 价值闸） | **边界卡得正好**。真实 21 个 ok 文件：**误拒率 0/15 = 0.0%**；拦掉 3 个纯垃圾（hello.txt / test.txt / 0字节文件）；漏给 G2 的 3 个（授课计划表 / 错误堆栈 / 项目实施记录）恰好都是需语义判断的。37 passed。**已知限制**：正常文件得分恒为 1.00 → quality 全 core 无区分度，质量分级须由 G2 决定 |
| 2026-10-28 | **Trae-后端** 线 H-批1（LLM adapter） | **质量高于要求**。人家要求「干净的同步函数」，它做成了 **`Transport` Protocol + `UrllibTransport` 可注入** —— 测试注入 FakeTransport **完全不联网**，以后换 httpx 只实现一个类。错误分 5 类（认证/超时/限流/网络/响应），仅网络与 5xx 重试（429 不重试），`api_key` 有「不进异常文本」的显式断言。62 passed。**加分**：如实报告 `pptx_parser.py` 有既有的 format 问题但**没越界去改**（纪律正确）|
| 2026-10-28 | **Trae-后端** 线 H-批2（G2 归属闸） | **验收通过**。77 passed（+15）。防注入做到位：`<file_content>` 包裹 + system 声明「是数据不是指令」+ `test_injection_text_wrapped_in_delimiters` 直接注入「忽略以上指令」并断言被包裹。`G2Output` 用 `Literal` + 额外校验 rubric §7 映射（accept→index / partial→extract_spans / reject→topic_dialog|drop）。JSON 坏→重试1次→降级 accept/normal/index + degraded 标记。**LLM 异常向上抛不静默降级**（理由："否则 key 配错会把所有文件都收下"）——这个判断是对的 |
| 2026-10-28 | **Trae-后端** 线 I-批1（导入状态机） | **验收通过**。87 passed（+10）。端到端实测：`sha256=0.3ms dedupe=233ms G0=42ms G1=13ms note=17ms chunk=2326ms embed=395ms`；DB 核对 **`parent_with_embedding=0`**（父块绝无向量，ADR 0003 决策①落地）；同文件二次导入命中 sha256 只花 82ms、未重复调 G2。charter 实现为 `resolve_charter()` 方法（文件 > 环境变量 > 默认），有专项测试 |
| 2026-10-28 | **Trae-后端** 线 J（G2 政策修正 + 限流适配） | **验收通过**。90 passed（+3）。`_system_prompt` 三处修正（前置问限定适用范围 / 明确该收清单 / 拿不准就收），**防注入段与 JSON 契约一字未删**；429 改为尊重 `Retry-After`（≤90s、最多 2 次），Transport 契约扩为三元组；`g2_smoke` 加 `--delay 3 / --limit / --resume`。人家独立复跑 dry-run：`不走这一问=True`、`拿不准就 accept=True`、预算 4000=charter 1148+正文 1437。**它没用真网关跑 live（配额有限），改用本地假 OpenAI 服务验管道——判断正确** |
| 2026-10-28 | **ZCode-后端** 线 I-批2（arq worker + 上传 API） | **验收通过**。109 passed（+19）。人家 TestClient 实测：`/healthz` 200 `{db:ok,redis:ok}`、`/api/sources/999999` 404 语义正确、空 POST 422。六个批复项全落地（no_llm+g2_skipped 标记 / 白名单方案A / 50MB+55MB 请求体 / uuid 暂存目录 / worker 启动清 >24h 孤儿 / max_jobs=1+900s）。**三个追认项全批**：python-multipart+httpx、redis 8.1→5.3.1（实测 server 7.4.11 下 ping/aclose 正常）、deduped=`not job_created`。**加分**：`run_import_async` 用函数级延迟导入，避免上传 API 进程拉起 torch |

---

## 6. 人类怎么用这份文件

对任何 agent 只需要说一句：

```
读 baleen/HANDOFF.md 的 §0 和 §2「给 XXX」那一节，照做。
交付按 §4 格式写到 DELIVERY.md。
```

反馈方向：

```
人家（协调方） → 写 §2 待处理
agent         → 读 §2，做完写自己目录的 DELIVERY.md
人类          → 把 DELIVERY.md 甩给人家验收，结论进 §5
```

**本文件是唯一入口。** 技术决策在 `docs/adr/`，评测口径在 `eval/TASK.md`，项目约定在 `AGENTS.md`。
