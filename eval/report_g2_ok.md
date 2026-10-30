# 评测报告 · split=all

split 版本：未记录（分层粒度：未记录）

**误拒率 0/15 = 0.0%　|　误丢率 0/1 = 0.0%**

---

## 1. 数据集概况

| 项 | 值 |
|---|---|
| 本 split 样本数 | 21 |
| 标注完成度 | 21 / 21 |
| 参与计算的样本 | 21 |
| 缺失预测（preds 无此 rel_path） | 0 |
| dev / test 分布（全库） | 9 / 12 |
| test 实际占比（split_meta.json） | 未记录 |
| (槽位, 判定) 组合数 / test 已覆盖 | 12 / 12 |

槽位分布：

| slot | 样本数 |
|---|---|
| 1 | 4 |
| 2 | 4 |
| 3 | 2 |
| 4 | 2 |
| 5 | 2 |
| 6 | 2 |
| 9 | 1 |
| 10 | 1 |
| 13 | 3 |

### 1.1 (槽位, 判定) 二维分布

v3 的分层粒度就是这张表：**每个组合在 test 里至少 1 条**，否则同一个槽里「该收」和「该拒」两种失败模式只会被考到一半。

| slot | 判定 | dev | test | test 覆盖 |
|---|---|---|---|---|
| 1 | accept | 3 | 1 | ✅ |
| 2 | accept | 2 | 1 | ✅ |
| 2 | partial | 0 | 1 | ✅ |
| 3 | accept | 1 | 1 | ✅ |
| 4 | accept | 0 | 1 | ✅ |
| 4 | reject | 0 | 1 | ✅ |
| 5 | accept | 0 | 1 | ✅ |
| 5 | reject | 0 | 1 | ✅ |
| 6 | accept | 1 | 1 | ✅ |
| 9 | reject | 0 | 1 | ✅ |
| 10 | accept | 0 | 1 | ✅ |
| 13 | reject | 2 | 1 | ✅ |

合计：12 个组合，test 覆盖 12 / 12。

## 2. 🔴 误拒率

**误拒率 = 0/15 = 0.0%**（human ∈ {accept, partial} 中 pred = reject 的比例）—— 好笔记被扔了。

## 3. 🔴 误丢率

**误丢率 = 0/1 = 0.0%**（human_next_action = topic_dialog 中 pred_action = drop 的比例）—— 该再生的话题被扔了。

## 4. 话题召回率

话题召回率 = 0/1 = 0.0%（human_next_action = topic_dialog 中 pred_action = topic_dialog 的比例）。

## 5. 混淆矩阵

行 = human，列 = pred（不含 pred 为空的行）。

| human \ pred | accept | partial | reject |
|---|---|---|---|
| accept | 14 | 0 | 0 |
| partial | 1 | 0 | 0 |
| reject | 1 | 0 | 5 |

## 6. 判定准确率与各类 P / R / F1

- 判定准确率：19/21 = 90.5%
- macro-F1：0.61

| 类别 | support | precision | recall | F1 |
|---|---|---|---|---|
| accept | 14 | 0.88 | 1.00 | 0.93 |
| partial | 1 | 0.00 | 0.00 | 0.00 |
| reject | 6 | 1.00 | 0.83 | 0.91 |

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
| 总 token | 65567 |
| 均值 token / 文件 | 3122.2 |
| P50 延迟 | 3305 ms |
| P95 延迟 | 6625 ms |

## 10. 🔴 非法组合清单

合法映射只有 `accept→index`、`partial→extract_spans`、`reject→topic_dialog`、`reject→drop`；其余组合说明预测器自相矛盾，**计入错误数**。

**非法组合 = 0 条；判定错误 = 2 条；去重后唯一错误 = 2 条（两类可能重叠）。**

无。

