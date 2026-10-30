# 评测报告 · split=all

split 版本：stratified-v3（分层粒度：slot_verdict）

**误拒率 3/18 = 16.7%　|　误丢率 1/2 = 50.0%**

---

## 1. 数据集概况

| 项 | 值 |
|---|---|
| 本 split 样本数 | 35 |
| 标注完成度 | 35 / 35 |
| 参与计算的样本 | 35 |
| 缺失预测（preds 无此 rel_path） | 0 |
| dev / test 分布（全库） | 17 / 18 |
| test 实际占比（split_meta.json） | 51.4% |
| (槽位, 判定) 组合数 / test 已覆盖 | 18 / 18 |

槽位分布：

| slot | 样本数 |
|---|---|
| 1 | 4 |
| 2 | 4 |
| 3 | 2 |
| 4 | 3 |
| 5 | 3 |
| 6 | 2 |
| 7 | 4 |
| 8 | 2 |
| 9 | 1 |
| 10 | 1 |
| 11 | 4 |
| 12 | 1 |
| 13 | 3 |
| 14 | 1 |

### 1.1 (槽位, 判定) 二维分布

v3 的分层粒度就是这张表：**每个组合在 test 里至少 1 条**，否则同一个槽里「该收」和「该拒」两种失败模式只会被考到一半。

| slot | 判定 | dev | test | test 覆盖 |
|---|---|---|---|---|
| 1 | accept | 3 | 1 | ✅ |
| 2 | accept | 2 | 1 | ✅ |
| 2 | partial | 0 | 1 | ✅ |
| 3 | accept | 1 | 1 | ✅ |
| 4 | accept | 0 | 1 | ✅ |
| 4 | reject | 1 | 1 | ✅ |
| 5 | accept | 0 | 1 | ✅ |
| 5 | reject | 1 | 1 | ✅ |
| 6 | accept | 1 | 1 | ✅ |
| 7 | accept | 1 | 1 | ✅ |
| 7 | reject | 1 | 1 | ✅ |
| 8 | reject | 1 | 1 | ✅ |
| 9 | reject | 0 | 1 | ✅ |
| 10 | accept | 0 | 1 | ✅ |
| 11 | reject | 3 | 1 | ✅ |
| 12 | reject | 0 | 1 | ✅ |
| 13 | reject | 2 | 1 | ✅ |
| 14 | accept | 0 | 1 | ✅ |

合计：18 个组合，test 覆盖 18 / 18。

## 2. 🔴 误拒率

**误拒率 = 3/18 = 16.7%**（human ∈ {accept, partial} 中 pred = reject 的比例）—— 好笔记被扔了。

## 3. 🔴 误丢率

**误丢率 = 1/2 = 50.0%**（human_next_action = topic_dialog 中 pred_action = drop 的比例）—— 该再生的话题被扔了。

## 4. 话题召回率

话题召回率 = 0/2 = 0.0%（human_next_action = topic_dialog 中 pred_action = topic_dialog 的比例）。

## 5. 混淆矩阵

行 = human，列 = pred（不含 pred 为空的行）。

| human \ pred | accept | partial | reject |
|---|---|---|---|
| accept | 14 | 0 | 3 |
| partial | 1 | 0 | 0 |
| reject | 1 | 0 | 16 |

## 6. 判定准确率与各类 P / R / F1

- 判定准确率：30/35 = 85.7%
- macro-F1：0.58

| 类别 | support | precision | recall | F1 |
|---|---|---|---|---|
| accept | 17 | 0.88 | 0.82 | 0.85 |
| partial | 1 | 0.00 | 0.00 | 0.00 |
| reject | 17 | 0.84 | 0.94 | 0.89 |

## 7. 质量分级被低估清单

判据：`human_quality` 比 `pred_quality` 高两档及以上（档次 core > normal > reference > junk）。

| rel_path | human_quality | pred_quality |
|---|---|---|
| _inbox/霸道总裁爱上我.doc | core |  |
| _inbox/8b8442659fd99c1bf9e8518ed19f687a.jpg | normal |  |
| _inbox/5e47312e6d3499a33f304a0bbdbf8ba3.jpg | normal |  |

## 8. 全错样本清单（mining list）

判据：判定与动作**都**判错。这些最该人工复看。

| rel_path | human | pred | human_reason |
|---|---|---|---|
| _inbox/霸道总裁爱上我.doc | accept / index | reject / drop | 文件名像小说，实际是霸总文题材的结构化解析笔记，含核心概念、人物模板、三幕结构与趋势判断，文件名不可作为判定依据 |
| _inbox/项目实施记录（一）.md | reject / topic_dialog | accept / index | 实训模板填出来的过程记录，全是按流程执行的汇报，没有技术判断或踩坑结论 |
| _inbox/8b8442659fd99c1bf9e8518ed19f687a.jpg | accept / index | reject / drop | 手写的AI应用开发知识提纲，四大板块覆盖RAG、大模型原理、Agent编排与鉴权，虽只有关键词无展开，但结构完整可当回顾 |
| _inbox/5e47312e6d3499a33f304a0bbdbf8ba3.jpg | accept / index | reject / drop | 手写的JavaScript入门笔记，含getElementById代码示例与数据类型三分类，篇幅短但内容准确可复用 |
| _inbox/软件开发项目大综合实训实践项目指导书.docx | partial / extract_spans | accept / index | 全文约七成是教学安排（适用专业、场地、指导团队、评分要求、预习思考题），可用内容集中在第四部分 |

## 9. 成本

| 项 | 值 |
|---|---|
| 总 token | 65567 |
| 均值 token / 文件 | 1873.3 |
| P50 延迟 | 1846 ms |
| P95 延迟 | 5526 ms |

## 10. 🔴 非法组合清单

合法映射只有 `accept→index`、`partial→extract_spans`、`reject→topic_dialog`、`reject→drop`；其余组合说明预测器自相矛盾，**计入错误数**。

**非法组合 = 0 条；判定错误 = 5 条；去重后唯一错误 = 5 条（两类可能重叠）。**

无。

