# AI 复核员 · 一致率报告(盲审)

> 生成:2026-11-18T16:05:17 · 模式:samples · 可比 25 条(排除 10 条:解析不了/模型没给判定) · **人工仍是最终裁决者**

## 结论

- **完全一致 24/25 = 96.0%**(三档 verdict 逐字相同)
- **方向一致 25/25 = 100.0%**(同收/同拒,partial 与 accept 视为同向)
- **分歧 1 条** → 下面裁决单,主人逐条裁决(这是唯一需要人看的地方)

解读:完全一致率衡量 AI 与人像不像一个标注者;分歧不是谁错了,是**主观边界案件**,裁决结果回流 labels.csv 才算闭环。

## 混淆表(人 × AI)

| 人\AI | accept | partial | reject |
|---|---|---|---|
| accept | 14 | 0 | 0 |
| partial | 1 | 0 | 0 |
| reject | 0 | 0 | 10 |

## 分歧裁决单(主人填「最终裁决」列)

| rel_path | 你的判定(理由) | AI 判定(理由) | 最终裁决 |
|---|---|---|---|
| `_inbox/软件开发项目大综合实训实践项目指导书.docx` | **partial**(全文约七成是教学安排（适用专业、场地、指导团队、评分要求、预习思考题），可用内容) | **accept**(课程技术文档类（指导书），含可直接复用的知识单元：完整的轻量化OCR系统技术栈选) |  |

## 排除项(解析不了/模型未给判定,不进一致率)

- `_inbox/20260905_181535_a3e501ad.pdf`:unreadable 解析不了(no_text_expected):扫描件 PDF 的 2 页都没能识别出内容;2 页识别失败;按无文本层处理
- `_inbox/20260906_105322_839e8a44.pdf`:unreadable 解析不了(no_text_expected):扫描件 PDF 的 2 页都没能识别出内容;2 页识别失败;按无文本层处理
- `_inbox/5e47312e6d3499a33f304a0bbdbf8ba3.jpg`:unreadable 解析不了(no_text_expected):视觉识别失败(LLMResponseError),按无文本层处理;可检查设
- `_inbox/8b8442659fd99c1bf9e8518ed19f687a.jpg`:unreadable 解析不了(no_text_expected):视觉识别失败(LLMResponseError),按无文本层处理;可检查设
- `_inbox/新建 文本文档.txt`:unreadable 解析 ok 但无正文
- `_inbox/某班考勤表（第8-10周）.xlsx`:unreadable 解析不了(unsupported_format):暂不支持 .xlsx 表格簿,请另存为 CSV,或把内容整理进 .md
- `_inbox/综合实践课程项目报告.doc`:unreadable 解析不了(unsupported_format):不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .
- `_inbox/附件2-1：某大学学生岗位实习管理人员(指导教师)巡访情况记录表.doc`:unreadable 解析不了(unsupported_format):不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .
- `_inbox/附件2-9：某大学学生岗位实习安全承诺书.doc`:unreadable 解析不了(unsupported_format):不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .
- `_inbox/霸道总裁爱上我.doc`:unreadable 解析不了(unsupported_format):不支持 .doc(旧版二进制格式),请用 Word/WPS 另存为 .
