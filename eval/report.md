# 评测报告 · split=test

split 版本：stratified-v2

**误拒率 0/7 = 0.0%　|　误丢率 0/1 = 0.0%**

---

## 1. 数据集概况

| 项 | 值 |
|---|---|
| 本 split 样本数 | 14 |
| 标注完成度 | 14 / 14 |
| 参与计算的样本 | 14 |
| 缺失预测（preds 无此 rel_path） | 0 |
| dev / test 分布（全库） | 21 / 14 |

槽位分布：

| slot | 样本数 |
|---|---|
| 1 | 1 |
| 2 | 1 |
| 3 | 1 |
| 4 | 1 |
| 5 | 1 |
| 6 | 1 |
| 7 | 1 |
| 8 | 1 |
| 9 | 1 |
| 10 | 1 |
| 11 | 1 |
| 12 | 1 |
| 13 | 1 |
| 14 | 1 |

## 2. 🔴 误拒率

**误拒率 = 0/7 = 0.0%**（human ∈ {accept, partial} 中 pred = reject 的比例）—— 好笔记被扔了。

## 3. 🔴 误丢率

**误丢率 = 0/1 = 0.0%**（human_next_action = topic_dialog 中 pred_action = drop 的比例）—— 该再生的话题被扔了。

## 4. 话题召回率

话题召回率 = 0/1 = 0.0%（human_next_action = topic_dialog 中 pred_action = topic_dialog 的比例）。

## 5. 混淆矩阵

行 = human，列 = pred（不含 pred 为空的行）。

| human \ pred | accept | partial | reject |
|---|---|---|---|
| accept | 6 | 0 | 0 |
| partial | 1 | 0 | 0 |
| reject | 1 | 0 | 6 |

## 6. 判定准确率与各类 P / R / F1

- 判定准确率：12/14 = 85.7%
- macro-F1：0.59

| 类别 | support | precision | recall | F1 |
|---|---|---|---|---|
| accept | 6 | 0.75 | 1.00 | 0.86 |
| partial | 1 | 0.00 | 0.00 | 0.00 |
| reject | 7 | 1.00 | 0.86 | 0.92 |

## 7. 质量分级被低估清单

判据：`human_quality` 比 `pred_quality` 高两档及以上（档次 core > normal > reference > junk）。

无。

## 8. 全错样本清单（mining list）

判据：判定与动作**都**判错。这些最该人工复看。

| rel_path | human | pred | human_reason |
|---|---|---|---|
| _inbox/项目实施记录（一）.md | reject / topic_dialog | accept / index | 实训模板填出来的过程记录，全是按流程执行的汇报，没有技术判断或踩坑结论 |
| _inbox/软件开发项目大综合实训实践项目指导书.docx | partial / extract_spans | accept / index | 全文约七成是教学安排（适用专业、场地、指导团队、评分要求、预习思考题），可用内容集中在第四部分 |

## 9. 成本

| 项 | 值 |
|---|---|
| 总 token | 0 |
| 均值 token / 文件 | 0.0 |
| P50 延迟 | 0 ms |
| P95 延迟 | 47 ms |

## 10. 🔴 非法组合清单

合法映射只有 `accept→index`、`partial→extract_spans`、`reject→topic_dialog`、`reject→drop`；其余组合说明预测器自相矛盾，**计入错误数**。

**非法组合 = 2 条；判定错误 = 2 条；去重后唯一错误 = 4 条（两类可能重叠）。**

| rel_path | pred_verdict | pred_action |
|---|---|---|
| _inbox/20260906_105322_839e8a44.pdf | reject | extract_spans |
| _inbox/【哲风壁纸】人物特写-动漫少女.jpg | reject | extract_spans |

