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
| small + **LLM 自校正** | **11 处提议 11 处全应用**（含上述全部+人名），零词典；〔补〕但清单外盲区未触碰：GrokBot 系×8/SpaceXSI/Dil 残留——C 只放大基础质量 | 28.4s + 1 次 LLM 调用 | worker 插一步 |
| **large-v3-turbo（GPU float16）** | **原生专名最佳**：GrokBot×4（其余方案全 0）、Electron×6、Bombadil✓、Dune×7；仍有 Brett Taylor(1) | **29.5s**（RTF 34.4x，显存 Δ2.4GB） | **模型名参数级**（1.6GB） |
| large-v3-turbo（CPU int8） | 同上 | 482.7s（RTF 2.1x） | — |
| large-v3（GPU float16） | 专名反而不占优（Google Maps 0、Electron 1） | **2753s（慢 2.7 倍实时）**+load 63min(在线下载) | 参数级但淘汰 |
| SenseVoice（独立 venv） | 中：Bombadil✓、Electron×5；〔补〕错渲染 ~10 处（Google Maps→Appps、Agent UIs→Asian Us、GrokBot→Groppa）+重复词伪影 | **57.4s**（CPU fp32，RTF 17.7x；GPU 变体未测） | 新引擎+依赖 |

关键数据：
1. **small 的英文原稿比中文笔记暴露的更烂**——常见开发术语（pull requests/lint rules）都错，专名只是冰山露出水面的部分
2. **LLM 自校正零词典可达**：11 修 0 漏，理由全部基于上下文知识（「与 next.js 对比的桌面应用框架，应为 Electron apps」）——主人「零词典零审批」要求实测可行
3. **turbo 是甜点**：准确率接近 large-v3、速度等同 small、显存 2.4GB（6G 卡无压力）；large-v3 full 在本卡上 2.7 倍实时+加载 63min，淘汰
4. 两个引擎都无法修 Bret Taylor——**音频事实是"布莱特"**，正确拼写 Bret 只有模型知识能兜 → LLM 校正 pass 不可或缺

## 选型建议

**`faster-whisper large-v3-turbo`（模型名参数级改动）+ `language=None` 自动检测 + LLM 自校正 pass（转写后、organizer 前）**

- 转写速度与现状持平（29.5s vs 28.4s），专名原生准确率大幅提升，残余由 LLM 知识兜底
- 接入改动量：①video_extractor 模型参数改 turbo + **language 改 None 自动检测**（模型 1.6GB 预置缓存，**禁止运行时在线下载**——本次 load 63min 教训，HF 需代理）；②worker 转写后插 LLM 校正（输出带修改留痕，纪律照旧）
- 预期：常见专名/术语类错字大幅下降，90% 目标可达性高；冷门人名是 ASR 物理长尾，由终审层兜底

### ⚠️ 补充发现（spike 期间实锤，与选型同权重）

**生产强制 `language="zh"` 对英文视频是灾难放大器**——8093（GrokBot 教程）是英文演讲，被按中文强解码后专名错字 ≥20 处，本批 70% 错字率的主要根源之一。三个参数级改动因此是**三件套**：turbo + language=None + LLM 校正。注意本 spike 各变体均以生产同款 zh 强解码跑通（横向对比有效），但 **90% 目标的绝对验收必须用 language=None 重测 turbo**——这是接入批的第一步。

## 已知限制

- turbo 的 GPU 尝试链在 spike 会话侧因 HF 超时未走通，主会话用**本地模型目录**补测（load 4.3s）——生产接入必须预置模型缓存，运行时在线下载在无代理环境不可用
- SenseVoice 耗时未测（独立 venv 输出格式与 judge 脚本不对齐）——不影响选型结论（turbo 已达标且零新依赖）
- LLM 裁判脚本对英文转写的 JSON 解析需调（no-json 两例），不影响 noun_scan 词法对比的有效性
- 对比表 TLA+ 一行因正则词边界对 `+` 失效不可信；Dune 在 small/turbo 各 6/7 次命中待人工听源核实（可能是真词）

## 后续

1. 接入实现（turbo 参数 + LLM 校正 pass）——主人拍板后另开批
2. 量效验证：接入后下一批视频导入，终审错字率 vs 本批 70%——#5 的量效闭环
3. 豆包对比（等主人分享 8093 给豆包）——commodity 总结 vs 管线产出的案例研究

## 〔补测合并〕spike 会话恢复后的增量数据（19:4x）

### C 的过修/漏修量化（三基座全测）

| 基座 | 提议/应用 | 修对代表例 | 残错（清单外盲区） | 过修 |
|---|---|---|---|---|
| small | 11/11 | Brett→Bret✓、ploycrests→pull requests、election→Electron、jang→jank | GrokBot 系×8、SpaceXSI、Dil | 0 |
| SenseVoice | 28/28 | Groropbot→Grok×4、Dunne→Dune、over loopop→outer loop | Dunne×2、lit rules×2 | **2**（SpaceXI→xAI ✗、Google Appps→Google Apps ✗） |
| turbo | 7/7 | brett→Bret✓、ploy crests→pull requests、lag and jang→jank | groffbot（未被提议） | 1（space xsi→xAI ✗） |

- **过修率 3/46 ≈ 6.5%，模式固定**：C 向自己的世界知识靠拢——"SpaceX"+Grok 语境就猜 xAI、
  Appps 修成常见词 Apps 而非 Maps。**三件套要加第四件：过修护栏**（候选：只收高置信修正/
  域白名单/修正理由必填复核——策略待主人拍板）。
- **漏修模式固定**：面目全非的错听（Dil、groffbot）C 认不出来——印证「C 是放大器不是修复器」，
  small+C 残错 ~9 ≈ turbo 裸奔的 2 倍多，**换基座不可被 C 替代**。
- 工程坑（已解）：推理网关把 max_tokens 烧在 reasoning 上（4000/8000 均 0 输出，16000 才出）；
  「让模型重发全文」必截断——**只出修改清单+程序化 replace 留痕**才稳（judge 脚本 no-json 两例同根因）。

### 下游消融（raw→parse_md_text→生产 organize_note→终审同款 judge，四臂同条件）

| 臂 | 整理稿错形态 | judge typos | 判读 |
|---|---|---|---|
| small | **GrokBot 错名×9**（organizer 把 5 种不一致错拼归一化成统一错名） | **0** | **judge 漏报主错**——LLM judge 单独把关不构成质量闸 |
| SenseVoice | GrokBot 错名×12、spacexi×1、Brett×1 | 3 | 错字最多，抓到一部分 |
| turbo | GrokBot 错 0、Brett×1 | **1**（Brett→Bret，**与 8093 终审 flag#1 精确同款**） | 工具链自校验通过 |
| turbo+C | GrokBot 错 0、Bret✓、Bombadil✓、**xAI×1（C 过修传导进笔记）** | 0 | C 前置修正后 judge 零 flag，过修也跟着进笔记 |

- **organizer 对错字只会归一化放大，不会救**：raw 5 种 GrokBot 错拼 → 笔记统一错名×9；
  raw 全对（turbo）→ 笔记全对。上游质量线性传导。
- judge 在整理稿通道才恢复判别力（8093 终审能抓 Brett/bombadill；raw 通道 0 抓）——对生僻专名仍盲。

### turbo GPU 显存窗口注记（两会话实测差异的归因）

- 主会话 29.5s（RTF 34.4x，Δ2.4GB）＝显存窗口友好时测得；spike 会话同参数在桌面占用顶满时
  （总需求 91.5%/6.1G）10min 无首段产出——GPU 100% 但功耗仅 57W，WDDM 显存超售 PCIe 页交换。
- **生产判据：预置模型缓存 + 转写前显存余量检查**；6G 卡上 turbo 贴着上限跑，行为随桌面负载波动。
  large-v3（+3.2G）则必然超售（2753s/2.7 倍实时已是不超售时的最好成绩），淘汰结论不受影响。

### 增量产物

`backend/tmp/spike_2026-11-22/`：`t_*_llmfix.json`（C 修正清单+应用留痕）、
`downstream_{small,sensevoice,turbo,turbo_c}.json`（整理稿+judge+已知错扫描）、
`noun_scan.json`、`t_turbo.json`（CPU int8 482.7s/155 段）、
脚本 `spike_{download,whisper,sensevoice,llm_fix,downstream,judge}.py`；
SenseVoice 缓存 `~/.cache/modelscope`（936MB），隔离 venv `backend/tmp/spike_venv`（未碰 backend/.venv）。

## 接入前置验证（2026-11-22，三件套实跑）

主人拍板先验证再接入。实测（同音频）：

- **turbo + language=None**：29.8s（自动检测零速度代价，显存 Δ2.3GB）
- **+ LLM 自校正**：9 处提议 9 处全中（含清单外：SpaceX SI→xAI、slow→slop、cloud agents→Claude agents、tech dep→tech debt）
- **四方案对比**：错误形态残留 small 5 种 → turbo+zh 2 种 → **turbo+None+LLMfix 0 种**；GrokBot 正确拼写 0→10 次
- ⚠️ 一条待主人裁决：LLM 把 GrokBot 修成 Grok×3（产品真名 vs 创作者品牌，可能是过度修正）
- 时间结论：每条视频新增开销 ≈1 分钟，可行性成立
