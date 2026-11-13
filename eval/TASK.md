# 评测脚手架任务书（baleen / eval-kit）

> **给 AI 编码代理的任务说明。开工前请完整读完，并且严格按 §六 的「执行阶段」做 —— 不要一次做到尾。**

---

## 〇、一句话任务

为 baleen 的**导入闸门**构建可复现的评测脚手架：特征扫描器、标注表、批量跑器、指标计算器。

**本任务不实现任何闸门。** 只构建"评测它"所需的资产。

---

## 一、背景：被测系统是什么

baleen 是个人智能笔记库。一次导入的处理流程：

```
文件上传
  ↓ 解析（pdf / docx / md / txt / html / 图片 OCR）
G0 解析闸 ── 确定性：能读出足够文本吗？扫描件？损坏？
  ↓
G1 价值闸 ── 启发式：字符数、结构信号、重复检测、样板词
  ↓
G2 归属闸 ── 一次文件级 LLM 调用：对着 charter 判「属不属于本库、有没有可复用知识」
  ↓
verdict = accept / partial / reject
  ↓
通过 → 切块（≤400 汉字，受 embedding 512 token 限制）→ 向量化 → 入检索库
```

### 一个必须先理解的前提

**G2 的判定对象不是「文件属于哪个学科」，而是「文件里有没有可复用的知识」。**

所以评测集必须包含**同一学科、判定相反**的配对样本：

| 样本 | 学科 | 判定 |
|---|---|---|
| 只有 20 行 `Traceback` 的报错日志 | 计算机 | **reject** |
| 带「现象 / 复现步骤 / 根因 / 修复」的 bug 记录 | 计算机 | **accept** |

这一对是整份评测集里最值钱的两行 —— 它们证明闸门判的不是"领域"，而是"价值"。

### 单用户场景下的策略（影响指标优先级）

baleen 是单用户库，**误拒的代价 ≫ 误收的代价**（收错了能删，拒错了你根本不知道丢了什么）。

→ 所以：**误拒率（false reject rate）是本项目第一指标**，不是总体准确率。

---

## 二、你只能改这些地方

| | 路径 |
|---|---|
| ✅ 允许创建/修改 | `eval/` 下所有文件（**除 `samples/`**） |
| ✅ 允许读取 | `AGENTS.md`、`README.md`、`eval/samples/` 下的文件 |
| ❌ 禁止修改 | `eval/samples/` 下任何文件（含**重命名、移动、改内容**） |
| ❌ 禁止修改 | `backend/`、`frontend/`、`docs/` 下的任何文件 |
| ❌ 禁止联网 | 阶段 1–3 全程不需要网络 |
| ❌ 禁止调用 LLM API | 阶段 1–3 全程不需要 |

---

## 三、交付物

### D1. `eval/rubric.md` —— 标注指南

必须包含：

1. **三档判定（accept / partial / reject）的判据 + 具体反例**
   反例要落到"哪一类文件"，不能写"低质量内容"这种废话。
2. **质量四级（core / normal / reference / junk）的定义**，每级给 3 个具体例子。
3. **六个边界情况的裁定规则**：
   - 报错日志（什么情况算笔记、什么情况算垃圾）
   - 聊天记录导出（有结论 vs 纯闲聊）
   - 扫描件 / 拍照笔记（OCR 质量差时怎么判）
   - 重复文件（内容相同、名字不同）
   - 只有标题没有正文
   - 表格为主的文件
4. **标注者自一致性检查方法**：隔 3 天重标 10 条，比对结果。
   如果两次不一致 → **改 rubric，不要改直觉**。

⚠️ `rubric.md` 是**草稿**，最终由人确认。你写完就停，等人审。

### D2. `eval/scripts/candidates.py` → `eval/candidates.csv` —— 特征扫描器

递归扫描 `eval/samples/`（含 `_inbox/` 子目录），对每个文件输出**一行特征**。

**严格 12 列，不增不删：**

| 列 | 定义 |
|---|---|
| `rel_path` | 相对 `eval/samples/` 的路径，统一用 `/` 分隔 |
| `size_kb` | 文件大小，KB，保留 1 位 |
| `char_count` | 成功抽取的纯文本字符数；抽不出填 `-1`（**不许填 0 冒充**） |
| `page_count` | PDF 页数；非 PDF 填 `0` |
| `table_count` | 抽取到的表格数 |
| `image_count` | 内嵌图片对象数 |
| `ocr_needed` | `true` 当 `char_count < 50` 且（`image_count > 0` 或 `page_count > 0`） |
| `has_code_block` | 是否含 fenced code / 4 空格缩进代码块；PDF 无法可靠判断 → 保守填 `false` |
| `lang_ratio_zh` | 汉字数 / `char_count`，0–1，保留 3 位 |
| `dupe_of` | 若与目录内另一文件 sha256 相同，填**那一个**的 `rel_path`，否则留空 |
| `longest_line` | 最长行字符数 |
| `heading_count` | 识别到的标题数（md 的 `#` / docx 的 Heading 样式 / PDF 的字号层级） |

**CLI：**

```
python eval/scripts/candidates.py --samples eval/samples --out eval/candidates.csv [--resume]
```

**解析依赖按后缀降级**：装不上 pymupdf 就把 PDF 的 `char_count` 填 `-1` 并在该行留痕，**不许整体崩溃**。

### D3. `eval/labels.csv` —— 标注表骨架

**严格 13 列：**

| 列 | 说明 | 谁填 |
|---|---|---|
| `rel_path` | 主键，与 candidates.csv 对齐 | **脚本预填** |
| `filename` | basename，**原样保留，不许清洗** | **脚本预填** |
| `ext` | 小写后缀，不含点 | **脚本预填** |
| `char_count` | 从 candidates.csv 复制 | **脚本预填** |
| `slot` | 槽位编号 1–14（见 §四） | **脚本留空** |
| `split` | `dev` / `test`，按 seed 确定性预分配（约 60/40） | 脚本预填，**人可覆盖** |
| `human_verdict` | `accept` / `partial` / `reject` | **人** |
| `human_quality` | `core` / `normal` / `reference` / `junk` | **人** |
| `human_note_type` | `concept` / `procedure` / `case` / `reference_data` / `log` / `chat` / `ad` / `other` | **人** |
| `human_value_score` | 1–5 整数 | **人** |
| `human_is_partial` | `true` / `false` | **人** |
| `human_reason` | **一句话，必填**，说明为什么这么判 | **人** |
| `human_valued_span` | `partial` 时填起止页码或行号，否则留空 | **人** |
| `human_next_action` | 系统接下来该干什么，四值见下 | **人** |

#### `human_next_action` 的四个取值（★ 判定与动作是两件事）

**「这份文件算不算笔记」和「接下来拿它干什么」是两个维度，不要混成一个格子。**

| 值 | 含义 | 系统后续 |
|---|---|---|
| `index` | 整篇就是笔记 | 切块 → 向量化 → 入索引 |
| `extract_spans` | 文件里有**能直接用**的片段 | 只抽那几段入索引 |
| `topic_dialog` | 整篇不能直接用，但**能引出值得学的话题** | 提取话题 → 和用户讨论 → 生成草稿 → 用户确认后入库 |
| `drop` | 读完全篇没有任何可展开的东西 | 直接丢，**不浪费对话** |

映射关系：

```
accept               → index
partial              → extract_spans
reject + 有可展开话题 → topic_dialog
reject + 无可展开话题 → drop
```

典型案例：《项目实施记录（一）.md》里全是按流程执行的汇报，**当笔记看零价值（reject）**，
但它含「Git 分支模型」「OCR 依赖链」这些可展开的话题 → **`topic_dialog`**。

> 这条区分的来源是一个关键判据：**这份文件是在「复述别人定的规则」，还是在「记录自己想明白的东西」？**
> 前者是工作记录（reject），后者才是知识（accept）。写得再详细也不能把流程当知识。

### D4. `eval/scripts/runner.py` —— 批量跑器

```
python eval/scripts/runner.py --samples eval/samples --out eval/preds.csv \
       --predictor fake|http --split all|dev|test [--resume] [--dry-run]
```

- `--predictor http`：调产品 API（未就绪时**报错退出**，不许假装成功）
- `--predictor fake`：`eval/scripts/predictor_fake.py` 里的规则桩
  —— 只做 G0 的确定性判断 + 关键词命中，**不做任何 LLM 调用**
- 输出 **10 列**：`rel_path, pred_verdict, pred_quality, pred_note_type, pred_score, pred_action, cost_tokens, latency_ms, error, raw_json`
  - **`pred_action` 必填**，四值 `index` / `extract_spans` / `topic_dialog` / `drop`，与 D3 的 `human_next_action` 对齐
  - 缺这一列，「误丢率」和「话题召回率」两个指标算不出来（见 D5）
- `--resume` 跳过已完成的 `rel_path`
- **单文件失败不许中断整体**，写进 `error` 列继续

### D5. `eval/scripts/metrics.py` —— 指标计算器

```
python eval/scripts/metrics.py --labels eval/labels.csv --preds eval/preds.csv \
       --out eval/report.md --split test
```

`report.md` 必须包含，**顺序如下**：

1. **数据集概况**：样本数、槽位分布、dev/test 分布、标注完成度（几 / 几）
2. **🔴 误拒率（置顶加粗）**：`human_verdict ∈ {accept, partial}` 中 `pred_verdict = reject` 的比例
   —— **好笔记被扔了**
3. **🔴 误丢率（置顶加粗）**：`human_next_action = topic_dialog` 中 `pred_action = drop` 的比例
   —— **该再生的话题被扔了**，这是比误拒更严重的错误，因为用户根本不会知道丢了什么
4. **话题召回率**：`human_next_action = topic_dialog` 中 `pred_action = topic_dialog` 的比例
   —— 衡量「一起把它变成笔记」这条再生路径的能力
3. **混淆矩阵**：3×3，行 = human，列 = pred
4. **判定准确率 + macro-F1**，以及各类的 precision / recall
5. **质量分级被低估清单**：`pred_quality` 比 `human_quality` 低两档以上的样本
6. **全错样本清单**（mining list）：这些是最该人工复看的
7. **成本**：总 token、均值 token/文件、P50 / P95 延迟
8. **🔴 非法组合清单**：`pred_verdict` 与 `pred_action` 不匹配的行必须单列并计数。
   合法映射只有四条：`accept→index`、`partial→extract_spans`、`reject→topic_dialog`、`reject→drop`。
   出现 `reject + extract_spans`、`accept + drop` 这类组合说明预测器自相矛盾，**计入错误数**。

**若 `labels.csv` 的 `human_*` 全空或未标完**：优雅报错并提示"还差 N 条未标注"，**不许崩**。

### D6.（阶段 4 才做）`eval/queries.json` + 检索评测

等产品检索功能就绪后再做，**现在不要动**。预告 schema：

```json
[
  { "qid": "q01", "query": "上次订单模块的并发 bug 是怎么解决的？",
    "relevant": ["代码/bug-20250301.md", "测试文档/附录C.md"] }
]
```

指标：Recall@5、MRR、nDCG@10。

---

## 四、14 个槽位（corpus slot）

样本按「**失败模式**」选，不按「文件类型」堆。每个槽 2–3 个文件，总共 30–50 个。

| # | 槽位（找什么） | 预期判定 | 为什么这个槽必须有人 |
|---|---|---|---|
| 1 | 结构良好的自写笔记（标题+列表+代码块） | accept / core | 基线正样本，没有它整体不可信 |
| 2 | 课程大纲 / 课件 PDF（页眉页脚水印多） | accept / normal | 解析噪声 |
| 3 | 论文 PDF（双栏、公式、参考文献） | accept / reference | 解析最难 |
| 4 | 测试用例表 / 测试计划（纯表格为主） | accept / core | 表格处理 |
| 5 | 需求规格说明书 SRS（多级编号） | accept / core | 结构保真 |
| 6 | 代码 README / CONTRIBUTING / 部署文档 | accept / reference | 代码 + 说明混合 |
| 7 | 扫描件 / 拍照笔记（OCR 质量差） | accept 或 partial | OCR 兜底 |
| 8 | 纯模板周报（只填了日期和名字） | reject / junk | 模板垃圾 |
| 9 | **无上下文的报错堆栈**（20 行 Traceback） | reject / junk | ⭐ 边界：领域内但无价值 |
| 10 | **带复现步骤的 bug 记录**（现象/根因/修复） | accept / core | ⭐ 与槽 9 配对 |
| 11 | 随手截图 / 表情包 | reject / junk | 图片 |
| 12 | 重复文件（内容一样、名字不同） | reject / junk | 去重 |
| 13 | hello world / 空文件 / 3 行 txt | reject / junk | 靶子 |
| 14 | 文件名误导（叫"论文.pdf"其实是发票） | reject / junk | 文件名不可信 |

另外脚本必须支持 **`--random N --seed 42`**：从全部候选中随机抽 N 个（默认 10），
代表"日常平庸文件" —— 真实使用里 80% 的导入就是这种，最容易被评测忽略。

---

## 五、硬约束（违反即返工）

1. **不许在 `labels.csv` 里预填任何 `human_*` 列**（除 `split` 外，第 5–13 列一律留空）。
2. **`candidates.py` 里不许出现任何「是否算笔记 / 是否有价值」的判断代码或阈值。**
   它只许**算特征**。判定是人的事 —— 一旦你替人判了，评测就变成"用模型测模型"。
3. **不许改 `eval/samples/` 下任何文件**，包括重命名。文件名本身就是评测特征。
4. **不许编造样本**。没有真实文件就留空，写 `NA`。
5. 所有随机操作固定 `seed = 42`，结果可复现。
6. 依赖只用：标准库 + `pandas` / `numpy` / `scikit-learn` + 解析库（`pymupdf` / `python-docx`）。
   要加别的先问。
7. **单文件失败不许中断整体。**
8. 阶段 1–3 不联网、不调 LLM。
9. **不许 `import` backend 内部模块。** runner 只通过 HTTP 调产品 API。
10. **代码规模约束（v3，人类已批准 1200）**：
    - 脚本总计 **≤ 1200 行**
    - **共享函数必须抽到 `_common.py`**，禁止在多个脚本里各写一份（重复代码会分叉，这是真问题）
    - 单个脚本超过 **500 行**时，在交付里说明为什么不能再拆即可，不强制拆
    - **任何 agent 不得自行放宽本节任何一条**。觉得不够用就停下来问。

---

## 六、执行阶段（**不要跳过，不要一口气做完**）

### 阶段 1（现在只做这个）

- `rubric.md`
- `candidates.py` + 跑出 `candidates.csv`
- `labels.csv` 骨架（前 5 列预填 + `split` 预分配）

**做完就停。** 等人人工标注 10 条并确认口径对齐。**不要继续做阶段 2。**

> 为什么必须停：如果 rubric 的口径错了，你后面写的 runner 和 metrics 全是白工。
> **先对齐口径，再自动化。**

### 阶段 2（人确认后）

- `runner.py` + `predictor_fake.py`，验收 = `--predictor fake --dry-run` 能跑通

### 阶段 3

- `metrics.py`，验收 = `labels` 全空时报错清晰不崩溃

### 阶段 4（产品检索就绪后，等人通知）

- `queries.json`（20 条）+ 检索评测脚本

---

## 七、验收标准

- [ ] `python eval/scripts/candidates.py` 在 `_inbox` 有文件时能产出 `candidates.csv`
- [ ] `eval/samples/` 下所有文件 sha256 **前后一致**（没被你碰过）
- [ ] `labels.csv` 的 `human_*` 列**全部为空**
- [ ] `rubric.md` 覆盖 14 个槽位 + 6 个边界裁定规则
- [ ] `candidates.py` 里搜不到任何形如 `is_note` / `valuable` / `score >` 的判定逻辑
- [ ] 脚本总计 ≤ 1200 行、共享函数无重复实现、随机种子固定
- [ ] 阶段 1 交付后**主动停下**，不再往下做

---

## 附录 A：charter 模板（写 rubric 时对照用）

产品侧每个知识库有一段自然语言 charter，G2 就是拿它当尺子。默认模板：

> 「这是一个**软件工程 / 软件过程方向学生**的学习知识库。
> **收**：需求与设计文档、测试计划与缺陷记录、项目过程记录（周报/评审/度量）、技术笔记与论文摘要、可复现的问题记录。
> **不收**：无上下文的报错堆栈、纯模板无信息量的周报、聊天记录、下载的发票说明书、纯截图。」

**注意这段里没有出现任何"领域枚举"** —— 只有用途 + 正例 + 反例。
写 `rubric.md` 时，判据必须能对上这段 charter 的每一条，一一对应。

---

## 附录 B：常见错误（前人踩过的）

| 错误 | 后果 |
|---|---|
| 把"看起来像垃圾"的文件直接标成 reject | 评测集失去区分度，准确率虚高 |
| 挑样本时只挑典型、不挑犹豫的 | 上线后 90% 的错在评测集里找不到 |
| 让脚本预填 `human_*` | ground truth 被污染，整个评测失效 |
| 一次性把 5 件交付物全做完 | 口径错一处，全部白工 |
| 把样本重命名成 `001.pdf` 这种 | 文件名特征消失，槽 14 直接失效 |
| 删掉 `pytorch_model.bin` 之前没确认 | （与本任务无关，但记得留 safetensors）|
fetensors）|
fetensors）|
