# 评测报告 · split=test，ok 口径（剔除 G0 解析失败）

split 版本：stratified-v3（分层粒度：slot_verdict）

**误拒率 0/17 = 0.0%　|　误丢率 0/1 = 0.0%**

---

## 1. 数据集概况

| 项 | 值 |
|---|---|
| 本 split 样本数 | 28 |
| 标注完成度 | 28 / 28 |
| 参与计算的样本 | 22 |
| G0 解析失败（剔出口径外） | 6 |
| 缺失预测（preds 无此 rel_path） | 0 |
| dev / test 分布（全库） | 43 / 28 |
| test 实际占比（split_meta.json） | 39.4% |
| (槽位, 判定) 组合数 / test 已覆盖 | 21 / 21 |

### 1.0 G0 解析失败清单

这些样本**不计入本报告的误拒率**（G0 是格式读取能力问题，不是判定闸判错）；它们单列在此，作为 G0 格式覆盖缺口的认账：

| rel_path | parse_status / 说明 |
|---|---|
| _inbox/20260906_105322_839e8a44.pdf | no_text_expected：未检测到文本层(扫描件或纯图 PDF),应走 OCR 流程;G0 已拦截,不进入切块 |
| _inbox/20260919_150620_eb5e8db3.pdf | no_text_expected：未检测到文本层(扫描件或纯图 PDF),应走 OCR 流程;G0 已拦截,不进入切块 |
| _inbox/【哲风壁纸】人物特写-动漫少女.jpg | no_text_expected：纯图片没有文本层;未配置 LLM(或 no_llm),跳过视觉识别,暂不入正文 |
| _inbox/附件2-1：某大学学生岗位实习管理人员(指导教师)巡访情况记录表.doc | unsupported_format：不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .docx 后再导入 |
| _inbox/霸道总裁爱上我.doc | unsupported_format：不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .docx 后再导入 |
| _inbox/8b8442659fd99c1bf9e8518ed19f687a.jpg | no_text_expected：纯图片没有文本层;未配置 LLM(或 no_llm),跳过视觉识别,暂不入正文 |

槽位分布：

| slot | 样本数 |
|---|---|
| 1 | 1 |
| 2 | 2 |
| 3 | 1 |
| 4 | 2 |
| 5 | 2 |
| 6 | 1 |
| 9 | 1 |
| 10 | 1 |
| 13 | 1 |
| 未填 | 10 |

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
| 未填 | accept | 23 | 8 | ✅ |
| 未填 | partial | 0 | 1 | ✅ |
| 未填 | reject | 3 | 1 | ✅ |

合计：21 个组合，test 覆盖 21 / 21。

## 2. 🔴 误拒率

**误拒率 = 0/17 = 0.0%**（human ∈ {accept, partial} 中 pred = reject 的比例）—— 好笔记被扔了。

## 3. 🔴 误丢率

**误丢率 = 0/1 = 0.0%**（human_next_action = topic_dialog 中 pred_action = drop 的比例）—— 该再生的话题被扔了。

## 4. 话题召回率

话题召回率 = 0/1 = 0.0%（human_next_action = topic_dialog 中 pred_action = topic_dialog 的比例）。

## 5. 混淆矩阵

行 = human，列 = pred（不含 pred 为空的行）。

| human \ pred | accept | partial | reject |
|---|---|---|---|
| accept | 15 | 0 | 0 |
| partial | 2 | 0 | 0 |
| reject | 2 | 0 | 3 |

## 6. 判定准确率与各类 P / R / F1

- 判定准确率：18/22 = 81.8%
- macro-F1：0.54

| 类别 | support | precision | recall | F1 |
|---|---|---|---|---|
| accept | 15 | 0.79 | 1.00 | 0.88 |
| partial | 2 | 0.00 | 0.00 | 0.00 |
| reject | 5 | 1.00 | 0.60 | 0.75 |

## 7. 质量分级被低估清单

判据：`human_quality` 比 `pred_quality` 高两档及以上（档次 core > normal > reference > junk）。

无。

## 8. 全错样本清单（mining list）

判据：判定与动作**都**判错。这些最该人工复看。

| rel_path | human | pred | human_reason |
|---|---|---|---|
| _inbox/软件开发项目大综合实训实践授课进度计划表.docx | reject / drop | accept / index | 抽取全文仅8行，全部是授课进度计划表的空白表头与签名栏，没有任何实际进度内容 |
| _inbox/项目实施记录（一）.md | reject / topic_dialog | accept / index | 实训模板填出来的过程记录，全是按流程执行的汇报，没有技术判断或踩坑结论 |
| _inbox/软件开发项目大综合实训实践项目指导书.docx | partial / extract_spans | accept / index | 全文约七成是教学安排（适用专业、场地、指导团队、评分要求、预习思考题），可用内容集中在第四部分 |
| _inbox/4b577ec72ee8_三分钟学会git和github.md | partial /  | accept / index | 来源=视频转写;转正:AI复核partial采纳(口语转写含概念性结论),主人2026-11-18批准;AI:B站视频的 |

## 9. 成本

| 项 | 值 |
|---|---|
| 总 token | 70973 |
| 均值 token / 文件 | 3226.0 |
| P50 延迟 | 4875 ms |
| P95 延迟 | 24555 ms |

## 10. 🔴 非法组合清单

合法映射只有 `accept→index`、`partial→extract_spans`、`reject→topic_dialog`、`reject→drop`；其余组合说明预测器自相矛盾，**计入错误数**。

**非法组合 = 0 条；判定错误 = 4 条；去重后唯一错误 = 4 条（两类可能重叠）。**

无。

