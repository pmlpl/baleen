# ADR 0006：图片笔记的视觉理解（G0 装眼睛 + 问答回传原图）

- 状态：**已采纳（2026-11-01 主人拍板：导入时看图 / 引擎用用户自配的视觉模型 / 问答命中回传原图）**
- 日期：2026-11-01
- 提出者：ZCode（主人「AI 看图」需求触发）
- 关联：ADR 0002（G0 曾判扫描件「应走 OCR」但 OCR 未做）、ADR 0005（阶段3/4）、2026 多模态 RAG 选型（VLM caption 路线）

---

## 1. 背景与痛点

图片文件此前能导入存档但内容是空壳：G0 见图片只能落 `[图片] alt` 占位块,
切块向量化的是空壳 → 检索命中不了、问答答不出。评测语料里两张手写笔记因此被
G0 拒收（HANDOFF 槽 7 一直缺 (7,accept)）。

主人确认其模型（deepseek-flash,DeepSeek 2026-08 V4-Flash 线）自带视觉,
「眼睛」不必新增依赖与 key——用用户自己配的 LLM 即可。

## 2. 方案选型（2026 主流四路线对照）

| 路线 | 结论 |
|---|---|
| **VLM caption 入库**（导入时看图转文字进索引） | ✅ **采纳**——「通用图片库+现有文本向量库」场景的标准答案;bge 纯文本底座零改动 |
| ColPali 视觉多向量检索(OCR-free) | ❌ 要推翻 bge/ADR 0004 检索底座,存储开销大,过度工程 |
| CLIP 类图文同空间 | ❌ 适配自然图片,文档/截图/手写一般,同样动底座 |
| OCR+VLM 双路索引 + Agentic 路由 | 🔭 远期演进,当前不需要 |

## 3. 设计

```
导入图片(jpg/jpeg/png/gif/webp/bmp/tif/tiff)
  → G0:parse_file(path, client=…)
      ├─ 无 client(没配 key / no_llm) → no_text_expected(旧语义不变)
      └─ 有 client → app/service/parse/vision.py:
          图片(≤10MB) → data URL → 视觉模型转写 Markdown → parse_md_text → blocks
          → ParseResult(ok, warnings=["图片内容由 AI 视觉识别转写…"], meta.engine=vision(<model>))
  → G1 价值闸(识别不出有价值内容自然被拒,不污染笔记库)
  → G2 归属闸 → 切块 → 向量化     ← 后半段零改动
```

**问答回传原图（caption 路线的后半步）**：检索命中的笔记若「来自图片文件」
（source.ext ∈ 图片集合）,按命中顺序最多取 2 张原图(≤10MB)读 blob 转 data URL,
附到最后一条 user 消息（OpenAI 视觉 content 数组）→ 视觉模型「看着补细节」。
联网兜底路径不附图（上下文来自搜索结果）。流式 meta 增加 `images_attached` 张数。

## 4. 降级与边界（验收口径）

1. **识别失败绝不拖垮导入**：LLM 报错/空 content/超限 → `no_text_expected` + message
   （`meta.vision_error` 留痕）,与「无文本层」旧语义一致
2. **转写内容照过 G1/G2**:垃圾识别被闸门拦住,不产生低质笔记
3. **不转发`.pdf` 扫描页**（需逐页栅格化,后续增量）——仅独立图片文件
4. **诚实标注**:识别产出的笔记 warnings 固定带「AI 视觉识别转写,可能有误差,可在编辑器修正」
5. **llm_content 不含图**:落库的 user 消息仍是纯文本(重放不依赖原图);原图只在当轮 prompt
6. 单次视觉调用走用户已配模型与 key,费用归属用户自己;no_llm 导入不烧视觉

## 5. 决策记录（主人拍板）

- 2026-11-01:「这两个和 AI 看图一起做」;纠正 deepseek-flash 自带视觉(旧认知作废)
- 2026-11-01:.env 当前激活的是内网 glm-5.2 网关,deepseek-flash 配置在注释里——
  e2e 用注释行(base_url=https://api.deepseek.com/v1)实测通过
- 已知注意:**推理型网关模型干不了视觉转写**(glm-5.2 经 sensenova 网关返回 6 字符空转写,
  G1 以「极短」拒收)——与 ADR 0004 整理线「需非推理模型」同源,识别模型跟用户配置走,不单独兜底
