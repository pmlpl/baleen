# HANDOFF — 线间交接台

> **单入口，各自出口。** 所有 agent **只读**本文件；交付写在你自己目录下的 `DELIVERY.md`。
> 这样不会出现两个 agent 同时写一个文件、互相覆盖的问题。

---

## 0. 谁读、谁写（先看这张表）

| 文件 | 谁写 | 谁读 |
|---|---|---|
| `HANDOFF.md`（本文件） | **只有人类 / 协调方** | **所有 agent 都读** |
| `backend/DELIVERY.md` | 只由**后端线**（ZCode）写 | 人类读 |
| `eval/DELIVERY.md` | 只由**评测线**（Trae）写 | 人类读 |
| `docs/adr/*.md` | 对应任务的 agent | 所有人 |
| `AGENTS.md` | **只有人类** | 所有 agent 读 |

**铁律**：agent **不许编辑 `HANDOFF.md`**。要回报就把内容追加到 `backend/DELIVERY.md` 或 `eval/DELIVERY.md`。

---

## 0.1 会话登记（主人给每个 agent 会话起的名字）

| 会话名 | 管哪些顶层目录 | 历史上做过 | 现在该发给它什么 |
|---|---|---|---|
| **ZCode-后端** | `backend/` | 线 A 骨架、线 B（ADR 0002 + G0 解析模块）、线 D 数据模型 | 所有 `backend/` 的任务：切块、向量化、导入状态机、检索、API |
| **Trae-评测** | `eval/` | 线 C 评测脚手架（阶段 1/2/3 + `stratified-v2`） | 所有 `eval/` 的任务：槽位分层、query 集、指标 |
| （未开）**前端** | `frontend/` | 只有骨架 | 主干打通后才开 |

### 怎么判断发新会话还是旧会话

| 情况 | 做法 |
|---|---|
| **同一个顶层目录的连续任务** | **接着用旧会话** —— 它知道这个目录的历史、为什么这么写 |
| 换顶层目录 | 开新会话（上下文不重叠，省 token） |
| 旧会话开始"忘记前文"（问已经做过的事、反复重读同一个文件） | 开新会话，开场第一句让它读 `AGENTS.md` + 本文件 |
| 一轮任务工具调用已经很多、还没交付 | 先交付再考虑换；中途换会话会丢进度 |

**判断标准一句话**：它还需要"上一轮为什么这么做"这个上下文吗？需要就留着，不需要就换。

### 命名规则（主人用）

```
{执行者}-{负责的范围}
例：ZCode-后端、Trae-评测、ZCode-前端
```

**同一个会话别改名**——名字一变，本表的记录就对不上了。要换范围就新开一个。

---

## 1. 当前状态

| 线 | 执行者 | 状态 | 最近交付 |
|---|---|---|---|
| **A 环境骨架** | ZCode | ✅ 已验收 | `docker-compose.yml`、`backend/` 骨架、`frontend/` 骨架 |
| **B 解析 ADR + G0 模块** | ZCode | 🟡 结构验收通过（未跑其测试、未读其逻辑） | `docs/adr/0002`、`backend/app/service/parse/` 8 文件、`tests/` + fixtures、`scripts/parse_smoke.py` |
| **C 评测脚手架** | Trae | ✅ 已验收（`stratified-v2`） | `eval/DELIVERY.md`、`make_split.py`、`_common.py`、`report.md` |
| **D 数据模型** | ZCode | ✅ 已验收（人家独立复跑确认） | `app/models/` 8 文件、`0002_data_model.py`、`repo/base.py`、`config.py` 补字段；**pytest 15 passed**、DB 实建 6 表、`halfvec(1024)`、pgvector 0.8.7 |
| **E 切块 + 向量化** | ZCode-后端 | ✅ 已验收（含 E-2 修复，人家用真实笔记复跑） | `app/service/chunk/`(601 行)、`app/service/embed/`、`scripts/chunk_smoke.py`；**29 passed**；真实笔记 in_range 11.6%→**64.4%**、缺陷反馈 0%→**100%**、over_limit/truncated 仍为 **0**；torch 2.14.1+cu126 @cuda 52 items/s |
| **F `.env` 加载** | ZCode-后端 | ✅ 已验收 | `config.py` 改绝对路径读根 `.env`；从任意 cwd 都能读到；**31 passed**（新增 `tests/test_config.py`）|

**文档现状**：`docs/adr/` 有 0001 / 0002 / 0003 / 0004，编号连续。

### 遗留待处理（不阻塞主干，但要记着）

1. **槽 7 分层要细到 (槽位, 判定) 粒度** —— 现在 test 里槽 7 只有发票，2 张手写笔记全在 dev，误拒率 0% 是假象
2. **切块参数要实验校准** —— 250–400 字是从 512 token 上限推导的，没做过实验；前置依赖是 20 个 query 集
3. **所有单测都用合成 fixture，没有一份真实样本进测试** —— 线 E 的碎片化就是这么漏掉的（27 passed 但真实数据 in_range 只有 11.6%）
4. **前端只有空白首页** —— 主干通了才动

---

## 2. 待处理

**本期暂无已开工任务。** 数据模型（线 D）已交付并验收，见 §1。ADR 0004（切块规则）已写好。

### ✅ 已拍板

| 决定 | 结论 | 日期 |
|---|---|---|
| `embeddings.vec` 写入方式 | **选 A：`uv add pgvector`**，用官方 `HALFVEC` 类型替换自定义 `HalfVec(UserDefinedType)` | 2026-10-28 |
| 向量化推理栈 | **GPU 版 torch**。本机 RTX 3060 Laptop / 6 GB 显存 / 驱动 616.56。**必须做 device 自动降级**（`cuda if torch.cuda.is_available() else cpu`），因为答辩机器可能没有 GPU | 2026-10-28 |

---

### 给 ZCode-后端（线 G：G1 价值闸）

```
先读：
1. baleen/AGENTS.md §5 决策 5/6/8（三闸分工、单用户默认收）
2. baleen/eval/rubric.md §2.1 三档判据（这是判定口径的来源）
3. baleen/app/service/parse/types.py（ParseResult 的形状）

背景：主干还缺两道判定闸门。**先做 G1**（纯启发式、零成本、不联网、不调 LLM），
它负责拦掉「明确无价值」的垃圾，模棱两可的一律放过交给 G2。

⚠️ 最重要的约束（AGENTS.md 决策 8）：单用户场景下【误拒代价 ≫ 误收代价】。
   → G1 只拦「明确垃圾」，**绝不因为"短/看不懂/没小标题"就 reject**。

任务（只动 baleen/backend/）：

1. app/service/gate/g1.py：judge(parse_result, ...) -> GateResult
   输出字段与 review_runs 表对齐：
     gate="G1", verdict(accept|reject), quality, score, action(index|drop), reasons[]

2. 打分信号（**每个信号都要能单独解释，落进 reasons**）：
   - 空/极短：去空白后 < 30 字符 → 直接 junk
   - 样板词命中：hello world / lorem ipsum / 这是一个测试 / 无标题 / 草稿 / test file → 直接 junk
   - 零结构：无标题、无列表、无表格、无代码块、且段落数 <= 1 → 强减分
   - 低信息密度：字符熵偏低 / 词型-词例比偏低 → 减分
   - 自我重复：全篇 shingle 重复率过高（同一句反复）→ 减分
   - 中文占比：**只记录，不参与判定**（避免误伤英文论文）

3. 判定规则：
   - 命中「直接 junk」→ reject / junk / drop
   - 综合得分低于阈值 → reject / junk / drop
   - 其余 **一律 accept**，按得分标 core / normal / reference

4. 阈值全部写成模块级常量，并加注释：
   「推导值，待 eval/labels.csv 的 35 条人工标注校准」

5. 单测（合成 fixture，**不许把 eval/samples 硬编码进测试**）：
   - "hello, world!" → reject / junk
   - 0 字节空文件 → reject / junk
   - "这是一个测试文件" 单行 → reject / junk
   - 613 行正常笔记 → accept
   - **3 行但有真实信息的 txt → 必须 accept**（这是关键反例，不许 reject）

不要做：不接 LLM、不写 G2、不碰数据库、不动 eval/、不改切块与解析。

自测（贴进 DELIVERY）：
- uv run pytest tests/service/gate -q
- 新增 scripts/g1_smoke.py --samples <路径>（缺目录就 skip），跑 eval/samples/_inbox 的 35 个样本，
  产出三张表：
    a) G1 判定分布
    b) 与 eval/labels.csv 的 human_verdict 对照的混淆矩阵
    c) G1 单独的误拒率（human accept/partial 中被 G1 判 reject 的比例）← 这个数论文要用
  ⚠️ 只读 eval/，一个字节都不许改
```
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

## 4. 交付写在哪

做完后，**追加**到你目录下的 `DELIVERY.md`（没有就建），格式：

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
