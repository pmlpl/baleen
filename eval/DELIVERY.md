# eval/DELIVERY — 评测线交付记录

> 岗位制：每个交付块标题写明「执行者」，同一个岗位换人不换记录。
> 历届担任：`Trae-评测`（线 C 全阶段 + `stratified-v2`）→ `DSH-评测`（线 N）。

## 2026-10-31 · G2 真跑评测（完整十节报告）+ 切块档位重跑（§8 校准）· 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `eval/preds_live.csv` | 真预测器逐行预测（35 样本，10 列同 runner.py COLUMNS）；`raw_json` 记判定阶段/reasons/降级标记 |
| 重新生成 | `eval/report.md` | **test split（held-out，18 条）**十节正式报告——从此是真数据，不再是 fake 占位 |
| 新增 | `eval/report_all.md` | 全量 35 条口径（含 14 个解析不了的非 ok 文件） |
| 新增 | `eval/report_g2_ok.md` | **ok 子集（21 条，G2 真正考核面）**口径——与 G1 的 0/15 同分母 |
| 追加 | `eval/ablation.md` §12 + §1 更新行 | 15 篇语料 × 线 R 回填后三档重跑（200/300/400 × 宽松/严格），判决维持 300 |

### 自测（原始输出）

- 真链路预测器 `backend/scripts/g2_eval.py`（parse→G1→G2，deepseek-flash 官方，probe json_mode=True）：
  35 样本 = **G0 拦 14 + G1 拦 3 + G2 判 18**，总花费 65,567 token（G2 均值 ~3.6k/条，1.8–7.1s），零 error 零降级。
- **误拒率（ok 口径，分母 15）= 0/15 = 0.0%**（基线 53.3% → 0%）；test split 2/10 与 all 3/18 的「误拒」
  全部是 **G0 解析不了**的 human-accept 文件（霸道总裁.doc + 两张手写 jpg，产品本就不进闸），非 G2 判错。
- 非法组合 **0**（18 条 G2 全部 accept→index / reject→topic_dialog|drop 合法）；误丢率 ok 口径 0/1。
- 唯一判定错误 = 1 条**误收**（项目实施记录（一）.md：human reject/topic_dialog → pred accept/index，test split）——
  「误拒代价≫误收」哲学下可接受，进 mining list。
- 消融：check_expected_paths **失配 0**（回填编号标题被 chunker 丢空 section、期望路径全存活，queries.csv 一字未改）；
  三档 R@5 94.4/94.4/88.9（宽松），300 档 R@10 唯一 100%、MRR 双口径最高 → **维持 300**。

### 已知限制

- 误丢率 all 口径 1/2（分母只有 2 条 topic_dialog，小样本无统计力）；macro-F1/混淆矩阵见 report.md。
- 600 档未跑（bge 512 窗口必截断，对 §8 的执行偏离已在 §12.4 存档）。
- 2 个原子代码块 >512 token 被 embedding 侧静默截（既有取舍，§1 结论 2 复现）。
- 300 档大节内 in_range 61.5%（<70% 目标、>60% 警戒线，4 篇小节型新笔记拉低，如实记录）。

## 2026-10-29 15:48 · 线 N-追加 · heading 前缀 A/B · 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `eval/scripts/prefix_ablation.py` | 三变体 A/B（V1 生产 / V2 text/table 补 §5 前缀 / V3 全量统一），纯内存、只读库、**不碰 `chunk/`** |
| 新增 | `eval/prefix_ablation.md` | 报告：结论先行 + 三变体指标表 + 逐条名次 + 「R@5 掉 / MRR 升不是矛盾」的解释 + 局限 + 复现 |
| 改 | `eval/scripts/ablation.py` | `ChildEntry` 加 `block_type` 字段 + `build_corpus` 填入（~2 行，**向后兼容**；既有 chunk 尺度消融数字不变） |
| 改 | `eval/ablation.md` §9.1 | 末尾加一行指向 `prefix_ablation.md`（A/B 已做） |

**没碰的**：`chunk/`（冻结区，零改动）、`backend/` 生产代码、`pipeline.py`、生产 embedding、`eval/samples/`、`labels.csv`。前缀注入在评测侧后处理 `child.text`。

### 自测

1. `uv run python ../eval/scripts/prefix_ablation.py` → exit=0，三变体出表。
2. **回归断言通过**：V1 = identity 变换，R@5=100.0% / MRR=0.653，与 `ablation.md` §11 宽松版 300 档逐位一致 → 「未改 chunker」得证。
3. 还原性断言：chunker 常量 = 300/250/400 ✓。
4. 子块 block_type 分布（309）：text 103 / code 178 / table 28。
5. 临时资源自查（HANDOFF §3.1）：不起服务器（纯内存检索 + 只读 `select`）；db/redis 是 `docker compose up -d` 起的常驻容器，非本次会话进程。

### 结果（主表）

| 变体 | R@5 | R@10 | MRR | ΔR@5 vs V1 |
|---|---|---|---|---|
| V1（生产） | 100.0% (18/18) | 100.0% | 0.653 | — |
| V2（text/table 补前缀） | 88.9% (16/18) | 100.0% | 0.730 | **−11.1pt** |
| V3（全量统一 §5） | 88.9% (16/18) | 100.0% | 0.660 | **−11.1pt** |

**结论**：给 text/table 裸加 ADR §5 前缀 **R@5 反掉 2 条**（q06 3→7、q15 4→8）；MRR 升是「已命中的排更前」（q04/q10/q16 跳到第 1 名），不是「多找到」。**ADR §5「显著提升判别力」断言不成立，不改生产**。V3 不比 V2 好 → code 现有 `# {hp} ({lang})` 前缀别动。详见 `eval/prefix_ablation.md`。

### 已知限制

- n=18，2 条 = 11.1pt，小样本；但方向（R@5 不升反降）足以否决「裸加前缀」。
- 只测纯向量路（生产是 trgm+RRF）；词法路可能救回 q06/q15——留作后续变体。
- 若以后想推进：试 query 侧加同构前缀对冲非对称副作用、或只给 table 加、或在混合检索下复测。

### 需要确认的问题

- （无）

---

## 2026-10-29 03:54 · 线 N-追加 · 人工复核落地 + q04 修订 + 消融重算 · 执行者：ZCode（主人批准）

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 改 | `eval/queries/queries.csv` | note 列补齐 20 条人工复核结论（**15 对 / 2 勉强 q07 q09 / 1 不对 q04 / 负例维持 2**）；q04 问法修订，`expected_*` 不变 |
| 改 | `eval/ablation.md` | 新增 §11「人工复核后重算」；§1 结论先行与 §8.2 局限挂更新指向 |

**没碰的**：`eval/samples/`（0 字节变化，笔记 #159 只读核对）、`eval/scripts/ablation.py`（**一行未改**）、`eval/labels.csv`、`backend/`、`frontend/`、`docs/`。`HANDOFF.md` 的两处更新不进本提交，按 HANDOFF §3.2 ② 走单独的协调提交。

> ⚠️ `queries.csv` 的 note 列有**两个写者**：DSH-评测会话先写了 q02/q04/q06–q20 的带行号复核，
> 本会话补齐 q01/q03/q05（带「（主人 2026-10-29）」署名）。两边结论**逐条一致**，仅格式/署名不统一。

### 自测

1. **人工复核**：主人依据 `eval/queries/复核单.md` 逐条判定（本会话收集，20/20 应答）。
2. **q04 修订依据**：笔记 #159（`eval/samples/_inbox/016-core-tools-test-coverage.md`，只读）§3.2 确无 monkeypatch 内容——monkeypatch 只在 §3.1 通用约定与 §八 经验，从不针对 registry。原问法前提在笔记里不存在，故改按用例表问。
3. **重算**（`ablation.py` 一行未改，只换 `--queries`；语料 11 篇 / 184 父块与原实验一致，两次均 exit=0）：
   - 宽松 n=18：R@5 94.4% / **100%** / 88.9%，MRR 0.613 / **0.653** / 0.625
   - 严格 n=16（临时 CSV 剔除 q07/q09）：R@5 93.8% / **100%** / 87.5%，MRR 0.627 / **0.672** / 0.641
   - 逐条名次**仅 q04 变化**（8/6/5 → 2/2/4），其余 17 条与原 §7 逐位一致（bge 确定性推理）
4. **结论变化**：300 档 R@5 = 100%（两口径一致）→「保持 300」变强；原 §6 读法 1「term 在 400 档最好」确认是 q04 坏标签伪象，已作废（可作答辩「为什么要人工复核」的实证）。

### 已知限制

- 严格版 query 集是 `%TEMP%` 临时文件（仓库内不落第二份 query 集避免双源），复现命令见 `eval/ablation.md` §11.4。
- q07/q09 的「勉强」属**锚点偏严**（答案部分在；q07 → 5.2、q09 → 4.3 可消），不影响结论。
- n=18/16、语料 11 篇的样本量局限原样适用；100% 是「小库小集上的满分」。

### 需要确认的问题

- （无）

---

## 2026-10-29 · 线 N · 20 query 集 + 切块消融 + 槽位分层细化 · 执行者：DSH-评测

### 产物

| 动作 | 路径 | 说明 |
|---|---|---|
| 新增 | `eval/queries/queries.csv` | 20 条 query（8 列：`qid,query,intent,expected_note_title,expected_heading_path,difficulty,kind,note`） |
| 新增 | `eval/queries/README.md` | 出题方法、`kind` 分类、**哪几条相关性是自判待复核**、命中口径 |
| 新增 | `eval/scripts/ablation.py` | 切块消融（纯内存，三档 × 向量检索；只读库、不改 backend） |
| 新增 | `eval/ablation.md` | 消融报告：三档 × 指标 + 块长分布 + 推荐 + 局限 |
| 改 | `eval/scripts/make_split.py` | 分层粒度 `slot` → **`(slot, verdict)`**；`stratified-v2` → **`stratified-v3`**；加锚定与差异表 |
| 改 | `eval/scripts/metrics.py` | 新增 **（槽位, 判定）二维分布表**（§1.1）；表头显示分层粒度与实际 test 占比 |
| 改 | `eval/labels.csv` | **只改 `split` 列**：4 行 `dev → test`，其余 13 列逐字节未变（见 C 项自检） |
| 改 | `eval/split_meta.json` | `version: stratified-v3` + `slot_verdict_pairs: 18` / `pairs_in_test: 18` / `test_ratio_actual: 0.5143` 等口径字段 |
| 改 | `eval/report.md` | 用 v3 split 重跑（test 14 → 18 条），关键数字见 C 项 |

**没碰的**：`eval/samples/`（0 字节变化）、`eval/labels.csv` 的 `human_*` 与 `slot` 列、`backend/`、`frontend/`、`docs/`、`.env`、`AGENTS.md`、`HANDOFF.md`、`eval/TASK.md`。**全程没跑任何 git 命令。**

---

### A. 20 个 query 集

**库里先查过：只有 3 篇，且都是早期为验证管道造的冒烟假数据**（`导入冒烟笔记` / `解析选型测试(Markdown)` / `Git Bash 常用命令备忘`）。
按任务书用 `import_smoke.py --no-llm --file` 从 `eval/samples/_inbox/` **只读导入** 8 份真实笔记
（**未复制、未改名、未移动任何样本文件**）：

| note_id | 标题 | 父块 |
|---|---|---|
| 156 | 闭包 —— 面试必问，装逼必备 | 20 |
| 157 | Fetch / Axios 完全指南 | 23 |
| 158 | 手写实战练习 —— 面试手撕题全集 | 34 |
| 159 | 变更 016：5 个核心工具补单测（…） | 19 |
| 160 | 缺陷反馈：VIP音乐播放能力缺失（最终版） | 11 |
| 161 | Agent 应用开发学习指南 | 48 |
| 162 | 项目实验实训报告（一） | 7 |
| 164 | 轻量化发票 OCR 识别与校验系统 | 15 |

**导入后库里 = 11 篇笔记 / 184 个父块 / 309 个子块（300 字档）**。那 3 篇假数据留在库里当干扰项，**没有拿来出题**。

**20 条构成**（每条 `note` 列都标了 `来源=库内笔记#id` 与复核标记）：

| kind | 条数 | qid | 考点 |
|---|---|---|---|
| `term` 术语精确匹配 | 5 | q01–q05 | 词法路：query 原样含 `__sign_hash_20200305` / `AbortController` / `MyPromise` / `_TOOL_META`+`discover_tools` / `max_history` |
| `paraphrase` 同义改写 | 5 | q06–q10 | 向量路：刻意避开原文专有名词（问闭包成因时不写"闭包"，问 token 刷新时不写"拦截器""401"） |
| `cross-section` 跨节综合 | 3 | q11–q13 | `expected_heading_path` 写两条（并发+取消 / 函数工厂+柯里化 / 防抖+节流） |
| `long` 长查询 | 3 | q14–q16 | ≥30 字，带背景与约束 |
| `short` 短查询 | 2 | q17 `内存泄漏排查`(6 字) · q18 `深拷贝`(3 字) | 极短 query 的信息量不足 |
| `negative` 负例 | 2 | q19 MySQL 间隙锁（近领域）· q20 红烧肉（远领域） | 库里确实没有，`expected_*` = `NA` |

**期望值不是编的**：每条 `expected_heading_path` 都是先跑
`select note_id, heading_path from chunks where is_parent` 抄下面包屑，再用
`select ... where text like '%术语%'` 确认术语落点；`ablation.py` 里还有一道 `check_expected_paths()`
自检 —— 期望路径在库里找不到就**直接报错退出**，不会算出一条假的"没命中"。

> ⚠️ **诚实说明：20 条的相关性全部由本代理（DSH-评测）自判，没有人工标注。**
> `eval/queries/README.md` §4 按"我有多确定"排了复核优先级：
> ① cross-section（3 条，一条 query 两个期望父块）→ ② long（3 条）→ ③ paraphrase（5 条）→ ④ term（最硬，抽查即可）。

---

### B. 切块消融实验

命令（**全量**，非 `--limit` 自测）：

```powershell
cd backend; $env:PYTHONPATH='.'; uv run python ..\eval\scripts\ablation.py
```

**语料 11 篇 / 184 父块 / 18 条正例 + 2 条负例。** 三档父块数都是 184（小节只由 heading/表格/代码边界决定，与档位无关），
**可比性来自这里。** query 侧走 `embed_query()`（带非对称前缀）、passage 侧走 `embed_passage()`（不加前缀），
余弦用点积（模型输出已 L2 归一化，未再归一化）。

| 档位 | 参数 TARGET/MIN/MAX | 子块 | 块长中位（全量 / 大节内） | **Recall@5** | **Recall@10** | **MRR** |
|---|---|---|---|---|---|---|
| 200 档 | 200 / 150 / 260 | 416 | 174 / **193** | 88.9% (16/18) | 100.0% | 0.592 |
| **300 档（= 生产值）** | **300 / 250 / 400** | **309** | 258 / **289** | **94.4% (17/18)** | **100.0%** | **0.634** |
| 400 档 | 400 / 380 / 480 | 271 | 287 / **372** | 88.9% (16/18) | 94.4% | 0.623 |

**按 kind 分项**：

| kind | n | 200 档 R@5/R@10/MRR | 300 档 | 400 档 |
|---|---|---|---|---|
| term | 5 | 80.0% / 100% / 0.825 | 80.0% / 100% / 0.833 | 100.0% / 100% / 0.840 |
| paraphrase | 5 | 80.0% / 100% / 0.622 | 100.0% / 100% / 0.533 | 80.0% / 80.0% / 0.618 |
| cross-section | 3 | 100% / 100% / 0.583 | 100% / 100% / 0.750 | 66.7% / 100% / 0.722 |
| long | 3 | 100% / 100% / 0.278 | 100% / 100% / 0.361 | 100% / 100% / 0.306 |
| short | 2 | 100% / 100% / 0.417 | 100% / 100% / 0.625 | 100% / 100% / 0.417 |

**推荐：保持 300 档（生产值），不要改。** 理由与保留意见（详见 `eval/ablation.md` §1 / §8）：

1. 300 档三个指标都不劣于另两档（R@5 +5.5pt、MRR +0.04 以上）；按 ADR §8 自己的判据
   （200 vs 400 的 R@5 差异 < 3% 就算"不敏感、维持 300"），实测差异 0.0pt → **判据也命中"维持 300"**。
2. **但 5.5pt 恰好 = 1 条 query**（18 条正例，1/18 = 5.6%），差异**不显著**。
   正确表述是"没有证据支持改"，不是"实验证明 300 最优"。三档差异几乎全部由 `q10` 一条造成。
3. **400 字确实是上限**：为把 400 档中位推到 372，`MAX` 已提到 480 字符（≈ `HARD_TOKEN_LIMIT`），
   p90 达 448 字符，而 R@5 反而掉 5.5pt —— 参数往上走收益为负、风险为正。
4. **顺带发现的真问题（与档位无关）**：三档都各有 **2 个代码子块 token > 512**（700 / 517 token，
   `block_type=code`），会被 bge **静默截断**。根因是 ADR §4.3「代码不受 `MAX_CHARS` 严格约束」，
   但 `import_smoke.py` 不打印 `warnings`，线上完全看不见。

**命中口径**（已写进 `eval/ablation.md` §3）：父块属于期望笔记 **且** `heading_path` 去空白后完全相等
或互为严格面包屑前缀（同级兄弟小节**不算**）；父块级排名，先取前 80 个子块再折父块；
相邻小节明明相关时**从严判为不命中**。负例因"纯向量检索永不出空集"**不计入 Recall/MRR**，
只单列 top1 相似度对比（三档都是：q19 近领域 0.498 ≈ 踩进正例分布，q20 远领域 0.269 ≪ 正例均值 0.597）。

---

### C. 槽位分层细化（stratified-v3）

**为什么改**：v2 的分层粒度是「槽位」一级，同一个槽里「该收」和「该拒」两种失败模式被混着分。
槽 7（扫描件/拍照笔记）就是典型：test 里只有一张扫描发票（reject），两张手写笔记（accept）全在 dev，
于是 **`误拒率 0%` 在 test 上是虚的** —— 一个见到扫描件就喊「拒」的傻瓜系统也能拿 0%。
v3 把粒度改成 **`(slot, human_verdict)` 二元组，要求每个存在的组合在 test 里至少 1 条**。

**改了什么**：

- `make_split.py`：核心约束从「each slot test≥1」改成「each `(slot, verdict)` test≥1」；
  保留原有约束（sha256 同 split / `partial` 全进 test / `slot 14` 进 test / `topic_dialog` 保底 / `--seed 42`）；
  新增「默认锚定输入文件里已有的 test，只做增量挪动」（`--no-anchor-existing` 可关）。
- `metrics.py`：新增 §1.1 **（槽位, 判定）二维分布**表（dev/test 条数 + 覆盖标记），表头显示 `stratified-v3` 与实际占比。
- `labels.csv`：**只改 `split` 列**。

#### 自检输出（照抄命令输出）

**① `(slot, verdict)` 覆盖矩阵 —— 18 个组合，全部 test ≥ 1**

```
[stratified] 样本 35，test 18 / dev 17（占比 51.4%，目标 40%，锚定已有 test=True）
  test: accept=9 partial=1 reject=8
  dev : accept=8 reject=9
  槽位覆盖：14 个槽，test 覆盖 14 / 14
  (slot, verdict) 覆盖矩阵：18 个组合，test 覆盖 18 / 18
    slot  1 · accept  test=1 dev=3
    slot  2 · accept  test=1 dev=2
    slot  2 · partial test=1 dev=0
    slot  3 · accept  test=1 dev=1
    slot  4 · accept  test=1 dev=0
    slot  4 · reject  test=1 dev=1
    slot  5 · accept  test=1 dev=0
    slot  5 · reject  test=1 dev=1
    slot  6 · accept  test=1 dev=1
    slot  7 · accept  test=1 dev=1
    slot  7 · reject  test=1 dev=1
    slot  8 · reject  test=1 dev=1
    slot  9 · reject  test=1 dev=0
    slot 10 · accept  test=1 dev=0
    slot 11 · reject  test=1 dev=3
    slot 12 · reject  test=1 dev=0
    slot 13 · reject  test=1 dev=2
    slot 14 · accept  test=1 dev=0
```

**② 总数核对：行数仍 35，test=18 / dev=17**（原定 `test_ratio=0.4`，实际 **0.5143**）

**③ 新旧 split 差异表 —— 正好 4 个文件 `dev → test`，0 个 `test → dev`**

```
  dev → test  _inbox/8b8442659fd99c1bf9e8518ed19f687a.jpg            ← (7, accept) 主人最关心的那条
  dev → test  _inbox/情景6 Spring Boot消息服务.pptx                    ← (2, accept)
  dev → test  _inbox/软件开发项目大综合实训实践授课进度计划表.docx       ← (4, reject)
  dev → test  _inbox/项目实验实训报告（一）.md                          ← (5, accept)
```

**④ `labels.csv` 逐列比对：只有 `split` 一列动了，`human_*` 与 `slot` 逐字节未变**

```
行数 35 -> 35 ；列数 14 -> 14
列名一致: True
发生变化的列: ['split']
  split: 4 处
    dev -> test   _inbox/情景6 Spring Boot消息服务.pptx
    dev -> test   _inbox/软件开发项目大综合实训实践授课进度计划表.docx
    dev -> test   _inbox/项目实验实训报告（一）.md
    dev -> test   _inbox/8b8442659fd99c1bf9e8518ed19f687a.jpg
受保护列（human_* + slot + 特征列）差异: 无 ✅
```

**⑤ 幂等 + 可复现**

- 再跑一次（对已更新的文件 `--dry-run`）→ `dev→test 0 条 / test→dev 0 条`（幂等）。
- 从 **v2 原始文件**出发、**关掉锚定**（`--no-anchor-existing`，即完全按 seed 42 从头贪心）→
  仍然得到**同一组 4 条 `dev → test`**。说明 v3 结果不依赖输入文件状态，锚定只是安全网。

**⑥ `report.md` 已用 v3 split 重跑，口径变了、数字也变了（这是对的，不是回归）**

| 指标 | 旧（v2 / test=14） | 新（v3 / test=18） |
|---|---|---|
| 参与计算样本 | 14 | 18 |
| **误拒率** | 0/7 = **0.0%** | **1/10 = 10.0%** |
| 误丢率 | 0/1 = 0.0% | 0/1 = 0.0% |
| 话题召回率 | 0/1 = 0.0% | 0/1 = 0.0% |
| 判定准确率 | 12/14 = 85.7% | 14/18 = 77.8% |
| macro-F1 | 0.59 | 0.53 |
| 非法组合 / 判定错误 / 去重后唯一错误 | 2 / 2 / 4 | 3 / 4 / 6 |

> **这就是 v3 的价值**：新进 test 的那张手写笔记（`8b844265….jpg`，人判 `accept/normal`）
> 被 fake predictor 判成 `reject/junk`（规则 `g0_no_text`），**误拒率从"虚的 0%"变成真实的 10%**。
> 旧口径下这个错误根本不在 test 里，看不见。

---

### 自测清单

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python eval/scripts/make_split.py --labels eval/labels.csv --dry-run` | 35 条 / test 18 dev 17 / 18 个组合全覆盖 / 4 条 dev→test |
| 2 | `python eval/scripts/make_split.py --labels eval/labels.csv` | 只改 `split` 列，`human_*` 与 `slot` 逐字节未变（上表 ④） |
| 3 | 幂等复跑 + 从 v2 原始文件不锚定复跑 | 均为同一组 18 条 test（上表 ⑤） |
| 4 | `python eval/scripts/metrics.py --labels … --preds … --split test` | exit=0，18 条参与计算，报告含 §1.1 二维分布表 |
| 5 | `cd backend; $env:PYTHONPATH='.'; uv run python ..\eval\scripts\ablation.py` | exit=0，全量三档跑完，出表（数字见 B 项） |
| 6 | `uv run python ..\eval\scripts\ablation.py --limit 2` | exit=0，自测开关可用（不出正式数字） |
| 7 | `python -m py_compile` + `uv run ruff check` | 三个脚本语法通过；`ablation.py` / `make_split.py` **ruff 全清**（`metrics.py` 剩的 E501/B023 是 v2 时代既有行，未动） |
| 8 | 收尾自查（HANDOFF §3.1） | 端口 8123 监听数 **0**；`uvicorn.exe` **0**；命令行含 `baleen` 的 `python.exe` **0**。全程未起任何服务器。8085 端口属 **Docker Desktop**（PID 22032，创建于 10-27，非本次会话），未动 |
| 9 | 数据库写入核对 | 消融脚本**只发 `select`**；唯一的写库是任务书授权的 `import_smoke.py` 导入 8 份样本 |

**没跑任何 git 命令**（`git status` 也没跑）。

---

### 已知限制

- **20 条 query 的相关性全部是模型自判，没有人工标注。** 期望判错会让三档一起错。复核优先级见 `eval/queries/README.md` §4。
- **消融的 n=18，1 条 query = 5.56pt**。所有 Recall 差异都是 1 条 query 的量级，**统计上全部不显著**；
  能站住的只是"300 档不劣于另两档"这个弱结论。
- **消融只测了纯向量路**，没测生产的 trgm + RRF 混合检索。术语类 query（q01–q05）在词法路上会更好，
  本次 80%～100% 的 `term` 成绩**不代表生产表现**。
- **没算 nDCG@10**：需要分级相关性标注，本 query 集只有二值，强算就是伪精度。ADR §8 原列的 nDCG 需等人工分级。
- **语料只有 11 篇 / 184 父块 / 59,403 字符**（父块正文去空白后 SQL 实测），其中 3 篇是合成冒烟数据（当干扰项用，未出题）。
- **`HARD_TOKEN_LIMIT` / `OVERLAP=0` / 表格按行切 / 代码按函数切 都没作为变量**；所以"代码块超 512 token"
  这类问题本实验测不出来，只能靠查库顺带发现。
- **`labels.csv` 的 `human_*` 一个字节没动**，所以 v3 的误拒率/准确率变化**完全来自 split 重分配**，不是重标注。
- `report.md` 里的 `P50/P95 延迟` 依赖 `preds.csv` 的墙钟值，每次重跑会变（与本次改动无关）。

---

### 需要确认的问题

1. **消融脚本注入三档参数的方式，请拍板。** `chunker.py` 用 `from .constants import TARGET_CHARS, ...`
   把参数读成**模块级名字**，没有任何配置注入点。我的做法是 `use_tier()` 上下文管理器
   **临时替换那三个模块级常量**、退出无条件还原、结尾 `assert` 已还原成 `300/250/400`。
   这是"不改源码也不改生产行为"的唯一办法（作用域受限、不落盘、进程结束即消失），
   但严格说等于**运行时改写模块属性**。若协调方不认可，替代方案只有"在 `eval/` 下复制一份参数化切块器"，
   那样会与生产实现分叉、测的就不是生产了 —— 我不推荐，但听协调方的。
2. **ADR 0004 §5 的"embedding 前拼接 `{标题} > {heading_path}`"在生产实现里根本没做**
   （`pipeline.py` 直接 `embed_passage([子块原文])`）。本次消融为忠实于生产，同样用原文。
   要不要补？补了就得改冻结的 `chunk/` 或 `pipeline.py`，**不在评测线权限内**。
   建议单开一条线，并且**先做"有前缀 vs 无前缀"的同口径 A/B** 再动手，别照 ADR 一句话就改。
3. **400 档的中位只到 372，不是 400**。若要严格贴 400，`MAX` 得提到 520+ 字符，
   那就超过 512 token 上限、进入静默截断区。**我按"不冒截断风险"停在了 `MAX=480`。** 若协调方要求严格 400，请明示。
4. **test 占比从 0.40 变 0.5143**（18/35）。覆盖面优先于比例，已如实记进 `split_meta.json`（`test_ratio: 0.4` 是诉求值，
   `test_ratio_actual: 0.5143` 是实际值）。**未超过 55% 上限。**
5. **库里现在有 11 篇笔记**（3 篇旧冒烟假数据 + 8 篇导入的真实样本）。假数据**没有一条 expected 指向它们**（未出题），
   但它们仍作为检索候选参与 top-k、可能挤占名次。**是否清理旧假数据（id 10/27/70）由协调方定 —— 我不删数据。**
6. **`eval/DELIVERY.md` 顶部 H1 还写着"评测线（Trae）"**。按 §0.3「绝不覆盖别人的块」，我没改它，请协调方统一。

---

## 2026-10-28 02:16 · 线 C · split 分层（stratified-v2）+ 计数口径 + 代码去重

### 产物

- `eval/scripts/make_split.py`（新增；`--strategy stratified --seed 42`）
- `eval/scripts/_common.py`（新增；`load_existing` / `write_csv` / `sha256_of` / `build_dupe_map`）
- `eval/scripts/candidates.py`、`eval/scripts/runner.py`（改用 `_common`，删掉各写一份的实现）
- `eval/scripts/metrics.py`（加 split 版本行；非法组合与判定错误去重计数）
- `eval/labels.csv`（**只改 split 列**）、`eval/split_meta.json`（新）、`eval/report.md`（新版）

### 自测

- `python eval/scripts/make_split.py --labels eval/labels.csv --dry-run`
  → 35 条，test 14 / dev 21；test = accept 6 / partial 1 / reject 7；14 个槽位每个在 test ≥1
- `python eval/scripts/make_split.py --labels eval/labels.csv`
  → 只改 split 列；与阶段 2 备份逐字段比对，差异只有 `split` 与你手改过的 `char_count`，
    `human_*`（13 列）与 `slot` 全部无差异
  → 配对 `错误堆栈.txt` ↔ `缺陷反馈_VIP音乐播放能力缺失.md` 同在 test；
    `20260919_...pdf` ↔ canonical `20260906_...pdf` 同在 test
- `python eval/scripts/metrics.py --labels eval/labels.csv --preds eval/preds.csv --split test --out eval/report.md`
  → 14 条参与计算；误拒率 0/7 = 0.0%；误丢率 0/1 = 0.0%；非法组合 2；判定错误 2；去重后唯一错误 4
  → 准确率 12/14 = 85.7%，macro-F1 0.59
- `python eval/scripts/metrics.py --labels <human_* 全空副本> --preds eval/preds.csv --split test`
  → exit=1，输出「还差 14 条未标注，暂不计算指标」+ 未标注清单，无 traceback
- 代码去重等价性（重构前后）：
  - `dupe_of`：旧 `fill_dupe(sha256 字典)` 与新 `build_dupe_map` 输出完全一致（35 行，唯一重复对相同）
  - `candidates.csv`：逐字节一致
  - `preds.csv`：除 `latency_ms` 外 9 列 + `raw_json` 逐一比对一致；`--resume` 与全量跑结果一致
  - `labels.csv`：`make_split.py` 重写前后逐字节一致（同 seed 可复现）
  - `report.md`：除「P95 延迟」随本次墙钟变化（56ms → 52ms）外，全文一致
- 代码总量：**1197 行**（`_common 48 + candidates 419 + predictor_fake 146 + runner 194 + metrics 219 + make_split 179`），在 1200 上限内

### 已知限制

- **test 里 `topic_dialog` 只有 1 条**（《项目实施记录（一）》），误丢率 / 话题召回率的分母是 1，
  数值只能当方向看，不能当比例结论。已加规则 7 保底，但样本本身只有 2 条 topic_dialog。
- **`partial` 在 test 只有 1 条**（《实践项目指导书.docx》），且 fake predictor 判成 accept，
  partial 类 F1 = 0，把 macro-F1 从 0.89 拉到 0.59；这是模型能力问题，不是 split 问题。
- **`preds.csv` 无法逐字节 diff**：`latency_ms` 是墙钟时间，每次跑都不同。已改为「除该列外全列比对」，
  其余 9 列 + `raw_json` 逐行一致。
- **扫描件 / 手写 jpg 无法 OCR**：fake predictor 对无文本层文件只能给
  `pred_verdict=reject` + `pred_action=extract_spans`（进待 OCR 队列），这正是 2 条非法组合的来源。
  产品口径若定为「待 OCR」就与 D5 的四条合法映射冲突（合法映射里 reject 只能接 topic_dialog / drop）。
- `.doc`（OLE2）、`.xlsx` 按 AGENTS.md 解析栈在 G0 直接拒收，不参与内容判定；
  但 `labels.csv` 里这两类的人工判定是按内容给的（如《霸道总裁爱上我.doc》= accept），
  所以指标里会稳定出现「误拒」，这不是预测器判错，而是格式能力缺口。
- 质量档次排序 `core > normal > reference > junk` 是我的口径假设（`reference` 视为查阅型资料，
  低于 `normal`）。若认可的不是这个序，「低两档」清单会变。

### 需要确认的问题

1. **非法组合与 `no_text_expected` 的口径冲突**（第二次提）：D5 的四条合法映射没有「无文本层、待 OCR」的出口。
   建议二选一并写进 TASK.md：① 合法映射补 `reject→extract_spans`（语义：待 OCR 抽取后再判）；
   ② 扫描件/图片一律判 `partial` + `extract_spans`。我目前按 ① 的语义输出，但没改 D5。
2. **误丢率只覆盖 `topic_dialog`**：若以后 `partial` 也算「可再生产」，分母口径要一起改。
3. **质量低两档的档次序**（见上）是否认可；不认可的话我改 `QUALITY_RANK` 一个常量即可。

### 踩到的坑（记一句）

最初把「无文本层」当成普通拒绝处理，于是扫描件/图片落到 `reject + drop`；
但 AGENTS.md 决策 9 是「默认收 + 标质量」，扫描件应进待处理队列而不是丢弃，
所以改成 `reject + extract_spans`——结果就撞上了 D5 的合法映射表。**根因是合法映射表缺少
「不可判但别丢」这一类动作**，不是预测器笔误。
