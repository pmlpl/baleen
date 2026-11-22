# 转写三方案 spike 报告（2026-11-22）

> 背景：实验批 10 篇视频 7 篇有错字（70%），whisper small 中文专名错听是根源（当年 3.1x 速度取舍）。
> 目标：转写正确率 90%+，**零词典、零人工审批**。执行：spike 会话（sess_bca425d4，中途模型层空回复两度失败，主会话接管收尾）。

## 基准

- 音频：8093（GrokBot 教程，1017s，32MB wav）——ground truth = 终审裁决错字清单 + 修正版笔记
- 生产同款参数（language="zh"）；失败链 cuda float16 → int8_float16 → cpu int8；显存 1s 采样

## 实测总表

| 方案 | 术语/专名表现 | 转写耗时(1017s 音频) | 接入成本 |
|---|---|---|---|
| **small（现状）** | **差**：pull requests→ploycrests×2、lint rules→lit rules×3、Electron→election×3、GrokBot→Grapbot、Brett Taylor(多一个 t) | **28.4s**（RTF 35.9x 实时） | — |
| small + **LLM 自校正** | **11 处提议 11 处全修 0 漏**（含上述全部+人名），零词典 | 28.4s + 1 次 LLM 调用 | worker 插一步 |
| **large-v3-turbo（GPU float16）** | **原生专名最佳**：GrokBot×4（其余方案全 0）、Electron×6、Bombadil✓、Dune×7；仍有 Brett Taylor(1) | **29.5s**（RTF 34.4x，显存 Δ2.4GB） | **模型名参数级**（1.6GB） |
| large-v3-turbo（CPU int8） | 同上 | 482.7s（RTF 2.1x） | — |
| large-v3（GPU float16） | 专名反而不占优（Google Maps 0、Electron 1） | **2753s（慢 2.7 倍实时）**+load 63min(在线下载) | 参数级但淘汰 |
| SenseVoice（独立 venv） | 中：Bombadil✓、Electron×5，耗时未测（输出格式差异） | — | 新引擎+依赖 |

关键数据：
1. **small 的英文原稿比中文笔记暴露的更烂**——常见开发术语（pull requests/lint rules）都错，专名只是冰山露出水面的部分
2. **LLM 自校正零词典可达**：11 修 0 漏，理由全部基于上下文知识（「与 next.js 对比的桌面应用框架，应为 Electron apps」）——主人「零词典零审批」要求实测可行
3. **turbo 是甜点**：准确率接近 large-v3、速度等同 small、显存 2.4GB（6G 卡无压力）；large-v3 full 在本卡上 2.7 倍实时+加载 63min，淘汰
4. 两个引擎都无法修 Bret Taylor——**音频事实是"布莱特"**，正确拼写 Bret 只有模型知识能兜 → LLM 校正 pass 不可或缺

## 选型建议

**`faster-whisper large-v3-turbo`（模型名参数级改动）+ LLM 自校正 pass（转写后、organizer 前）**

- 转写速度与现状持平（29.5s vs 28.4s），专名原生准确率大幅提升，残余由 LLM 知识兜底
- 接入改动量：①video_extractor 模型参数改 turbo（模型 1.6GB 预置缓存，**禁止运行时在线下载**——本次 load 63min 教训，HF 需代理）；②worker 转写后插 LLM 校正（输出带修改留痕，纪律照旧）
- 预期：常见专名/术语类错字大幅下降，90% 目标可达性高；冷门人名是 ASR 物理长尾，由终审层兜底

## 已知限制

- turbo 的 GPU 尝试链在 spike 会话侧因 HF 超时未走通，主会话用**本地模型目录**补测（load 4.3s）——生产接入必须预置模型缓存，运行时在线下载在无代理环境不可用
- SenseVoice 耗时未测（独立 venv 输出格式与 judge 脚本不对齐）——不影响选型结论（turbo 已达标且零新依赖）
- LLM 裁判脚本对英文转写的 JSON 解析需调（no-json 两例），不影响 noun_scan 词法对比的有效性
- 对比表 TLA+ 一行因正则词边界对 `+` 失效不可信；Dune 在 small/turbo 各 6/7 次命中待人工听源核实（可能是真词）

## 后续

1. 接入实现（turbo 参数 + LLM 校正 pass）——主人拍板后另开批
2. 量效验证：接入后下一批视频导入，终审错字率 vs 本批 70%——#5 的量效闭环
3. 豆包对比（等主人分享 8093 给豆包）——commodity 总结 vs 管线产出的案例研究
