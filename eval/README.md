# eval 说明书（自己跑）

> 评测 = 出考卷 + 判卷 + 看分数。这里只有一件事要记：**一条命令规则**。

## 一条规则

所有脚本都在 `baleen\backend` 目录下用 `uv run` 跑（PowerShell）：

```powershell
cd C:\Users\MOM\Desktop\学习项目\智能笔记库\baleen\backend
uv run python ..\eval\scripts\<脚本名>
```

脚本路径已经锚定自身位置，从哪跑都不会断；不这样跑会退回系统 Python 报缺模块（脚本会给提示）。

## 目录四个，各一句话

| 目录 | 是什么 | 能不能删 |
|---|---|---|
| `inputs\` | 考卷原料：样本、金标准标注、候选池 | **不能**（金标准丢了要重标） |
| `queries\` | 题集：正例 20 + 负例 8 | **不能**（人工标的期望） |
| `runs\` | 脚本产物（preds/报告/区间） | **随便删**，跑一下就回来 |
| `analysis\` | 结论：消融、缺陷台账 | 不删（论文引用） |

## 自己跑一遍（按序 6 步）

| 步 | 命令（接上面的 cd） | 出什么 | 看哪行 | 花钱 |
|---|---|---|---|---|
| 1 | `uv run python ..\eval\scripts\runner.py` | `runs\preds.csv`：35 行**占位**预测 | 终端 `[ok] 35 行` | 免费，秒级 |
| 2 | `uv run python scripts\g2_eval.py` | `runs\preds_live.csv`：35 行**真** G1/G2 判定 | 终端逐行判定 | **花 ~7 万 token** |
| 3 | `uv run python ..\eval\scripts\metrics.py` | `runs\report.md`：十节报告 | 文件头部加粗的**误拒率** | 免费 |
| 4 | `uv run python ..\eval\scripts\bootstrap.py` | 屏幕打印：每个数字带**区间** | 误拒率 `[下界, 上界]` | 免费 |
| 5 | `uv run python ..\eval\scripts\ablation.py` | **屏幕打印**（不写文件）：切块三档对比 | `[C]` 三档 R@5 表、`[E]` 逐条名次 | 免费，GPU 几分钟 |
| 6 | `uv run python ..\eval\scripts\negative_eval.py` | `runs\negative_report.md`：负例行为表 | PASS/FAIL（全 PASS=正常） | 免费；`--live` 要后端+token |
| 7 | `uv run python ..\eval\scripts\review_agent.py` | `runs\ai_review.csv` + `runs\ai_agreement.md`：**AI 盲审 35 样本 + 一致率 + 分歧裁决单** | 一致率行 + 分歧裁决单（你只裁决分歧） | 花 LLM token（用你设置页配的模型；`--mode candidates` 盲审候选池，`--compare` 免费重算一致率） |
| 8 | `uv run python ..\eval\scripts\frame_eval.py` | 帧选择质量打分：无用帧混入率 / 有用帧召回率 / 零帧清单 | 三个汇总数字 + 逐条 PASS/FAIL；**FAIL 是题集抓到的真问题，不是脚本坏了** | 免费（只读库） |
| 9 | `uv run python ..\eval\scripts\quality_scan.py --stats --scan` | 终端：全库分层统计 + 启发式嫌疑（错字/废帧/口语残留） | `[scan]` 行的条数；明细在 `runs\quality_scan_<日期>.csv` | 免费（只读库） |
| 10 | `uv run python ..\eval\scripts\quality_scan.py --llm` | CSV 追加 LLM 终审判定（确认/否决启发式 + 新发现） | 逐篇 `typo= frame= style=` 行 | **花 token（全库每篇 ≤1 次调用，44 篇约 12 万 token）；`--resume` 断点续跑** |
| 11 | `uv run python ..\eval\scripts\quality_scan.py --report` | `runs\quality_scan_<日期>.md`：**分诊报告**（只列 flag + 量化指标 + 主人抽查包） | flag 清单 + 抽查包两节 | 免费 |

**全库质检（2026-11-21 新增）**：AI 筛 100%、主人只看 flag 的分诊流程。步 9 免费可单独跑；步 10 是
成本闸（每篇 ≤1 次调用），步 11 把 CSV 汇成报告。启发式词典在脚本头 `DOMAIN_DICT`/`COMMON_WORDS`
（领域新词报了近似词误报 → 加进词典即可）。**只 flag 不修正**——主人裁决后另开修正批（PUT 修改留痕）。**v2 待办**（主人抽查实锤的维度缺口，
见脚本 docstring）：①图片内容冗余检查（alt 与所在章节正文重复，6095 §1.3 两截图实锤漏报）②代码段落
形态检测（JSON/HTTP 形态段落但无 codeBlock → 拍平嫌疑，161 §5.2 实锤=11-19 前旧 chunk 序列化存量，修复已在待重切块）。

**帧选择评测（backlog #6）的题集**在 `eval/queries/frame_selection_expectations.md`——主人 2026-11-19
对 7 条视频笔记的逐帧裁决（16 条期望）。⚠️ **存量笔记是旧筛选规则产物**，跑出来是「旧规则基线」；
要看新规则成绩必须先删旧笔记重导（防 dedupe）再跑。


**渲染回归探针（前端，不在上面 6 步里）**：
```
node scripts\probe_section_render.mjs
```
看到什么算对：最后一行 `全部通过`、退出码 0。覆盖 11-19 修的 6 个父块渲染分支
（围栏/无序/有序/引用/粗体/行内代码）+ 老笔记兼容 + XSS 底线，共 35 条断言。
**改 `frontend/src/utils/section.ts` 后必须跑这个**——否则渲染拍平类问题会静默回归。

**依赖关系**：步 3 吃步 1/2 的 preds；步 4 吃步 2 或 3；步 1、5、6、7 互相独立。
步 3 的报告默认用步 1 的占位 preds——**要看真成绩，用步 2 的**：
`uv run python ..\eval\scripts\metrics.py --preds ..\eval\runs\preds_live.csv`

## 该看到的数字（2026-11 基线，供对照）

| 指标 | 基线值 | 出自哪步 |
|---|---|---|
| G2 误拒率（真跑，ok 口径） | **0/15 = 0%**（基线曾 53.3%） | 步 2+3 |
| 误拒率置信区间（ok 口径全量） | **0/47 = 0%，95% CI [0%, 7.5%]** | 步 4 |
| 误拒率置信区间（ok 口径 test 留出集） | 0/17 = 0%，95% CI [0%, 19.5%] | 步 4 |
| 检索 R@5（300 档生产配置） | 94.4%，CI [83%, 100%] | 步 5 |
| 负例行为 | 8/8 PASS（瞎引即 FAIL） | 步 6 |

数字和基线差一点别慌：真跑（步 2）是 LLM 判定，有微小波动是正常的；差很多才需要叫我。

> **关于误拒率「0%」怎么读（2026-11-19 修）**：47 条样本里 0 条误拒，**不等于**真实误拒率是 0。
> 正确说法是「0/47，95% 置信区间 [0%, 7.5%]」——真实值可能到 7.5%，只是样本太少没抓到。
> 脚本里比例类指标（误拒率/误丢率/话题召回/判定准确率/Recall@k）一律用 **Clopper-Pearson 精确区间**；
> 早先用百分位 bootstrap 会在零事件时报 `[0%, 0%]`（等于宣称「必然是 0」），那是错的，已修。

## 什么时候该重跑

- **改了导入闸门/检索/organizer 的代码** → 步 2+3+6（回归）
- **加了样本/改了标注** → 全跑
- **只是想看成绩** → 步 3（不动代码不花钱）

## 不能删清单（防误删）

`inputs\` 全部（labels.csv 金标准 / samples 样本 / candidates 候选池）、`queries\` 全部、`analysis\` 全部、`scripts\` 全部、`TASK.md`、`rubric.md`。
`runs\` 里的一切都可以删——它们是脚本跑出来的，跑一下就回来。
