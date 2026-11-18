# 候选池复核裁决单(35 条)

> AI 盲审 42 条 vs 导入时机器判定;一致的扫一眼过(不在本单),
> **分歧/未判的置顶,主人逐条裁决**(转正=挑中的挪进 samples + 登记 labels.csv)。

- 机器与 AI **一致**:35 条(预筛通过,若都同意可整体转正)
- **分歧**:6 条(重点看)
- AI 未判(视觉/解析失败):1 条

## 一致清单(扫一眼)

| 文件 | 机器判定 | 质量 | AI判定 | 一句话理由 |
|---|---|---|---|---|
| `07a22535a905_´úÂëÖÊÁ¿Îå¹¦¿Î¿Ú²¥¸å.md` | accept | core | accept | 这是一份自成体系的技术方法论笔记：命名、函数设计、注释、测试金字塔、重构三定律五个主题都给出 |
| `087efedeca46_手测导入测试.md` | accept | core | accept | 虽自称测试笔记，但正文含可独立复用的系统事实——baleen 导入自动入库无需 F5、no_ |
| `14c1bf74b523_2026-11-05-给我讲讲和图片内容相关的知识.md` | accept | core | accept | 技术学习笔记，含多个可独立复用的知识单元：Coze/respond.io/DataWorks |
| `1d0de9ac168b_去看这个SpaceX AI工程师1小时的课程。 今晚别刷Net` | accept | core | accept | 这是 Lauren Tan 关于 AI agent 工程的演讲完整转录，含可独立复用的知识单 |
| `20598d6f8ed6_2026-11-18-闭包到底是什么？用大白话解释一下.md` | accept | core | accept | 技术笔记类文件，完整讲清闭包定义、形成条件、循环绑定事件陷阱的三种修法（let/IIFE/f |
| `292b9528bcc3_读懂策略互动、利益博弈的底层逻辑丨《博弈论》深度解读.md` | accept | normal | accept | 这是一份博弈论科普视频的完整转录，含囚徒困境、美元拍卖陷阱、密封二价拍卖与激励相容、机制设计 |
| `2c6ee4927612_【官方 MV】Never Gonna Give You Up ` | reject | junk | reject | 内容为 Rick Astley MV 的逐段画面描述加歌词转录（疑似自动生成的视频字幕/截图 |
| `40ce764fcf54_2026版LangChain教程，langchain快速入门，` | reject | junk | reject | 这是B站课程宣传视频的口播转写稿，通篇是营销话术（就业前景、公众号引流、一键三连）和口语填充 |
| `42c21bf947e6_去看这个SpaceX AI工程师1小时的课程。 今晚别刷Net` | accept | core | accept | 这是一份56分钟技术访谈的完整字幕转录（Lauren Tan 讲 agent 信任、spec |
| `52e6d0fd9f4e_你需要多少钱，才能财富自由？.md` | accept | core | accept | 视频转录虽错别字多，但核心知识单元完整可辨：财富自由公式(480X+K·n+M+H)、关键参 |
| `54dc1cd8745f_2026-11-15-圆锥曲线基础与椭圆错题解析.md` | accept | core | accept | 这是自写学习总结而非过程记录，含可直接复用的知识单元：椭圆定义/标准方程/离心率公式、中点弦 |
| `5880ebf34165_【AI Infra 一小时入门】GPU、CUDA、vLLM、P` | accept | normal | accept | 系统讲解 AI Infra 核心概念（TTFT/TPOT、KV Cache 预算与调度、吞吐 |
| `5bc2ce6f566d_kube_evolution.md` | accept | normal | accept | 自写的容器化技术栈学习总结，含多个可直接复用的知识单元（Dockerfile 分层缓存设计、 |
| `64812a6bc2e6_黑马程序员前端JavaScript入门到精通全套视频教程，ja` | reject | junk | reject | 文件名严重误导：除开头几行课程广告文案外，正文实为一段卡车司机日常行车vlog的语音转写（停 |
| `64b799d43a0c_TED演讲：不要轻信自己的所有想法！ p02 英语字幕.md` | accept | core | accept | 这是一份完整的 TED 演讲英文字幕转录，主体内容连贯可独立复用（限制性信念'ropes'的 |
| `68d87f720d95_2026-11-15-电磁场中屏蔽势与电荷分布推导.md` | accept | core | accept | 自包含的物理推导笔记，有可复用的知识单元（泊松方程求屏蔽势电荷分布的完整方法：球坐标Lapl |
| `7cfc76cb95a1_2026-11-18-RAG-是什么？它解决大模型的什么问题？` | accept | core | accept | 技术学习笔记而非过程记录：RAG 定义、与微调的多维度对比表、选型决策流程图、两阶段搭建流程 |
| `896361e192ac_你需要多少钱，才能财富自由？.md` | accept | core | accept | 视频转写稿但含完整可复用的知识单元：财富自由公式(480×月开销+育儿/医疗/购房各项)及其 |
| `8e04f0af1f75_2026-11-18-为什么不能用-JSON.parse(JS` | accept | core | accept | 完整的技术笔记：系统列出 JSON 深拷贝的类型丢失/报错清单（undefined/Symb |
| `95f379a65451_「Github一周热点137期」快速看懂 DeepSeek s` | accept | normal | accept | 技术视频转写稿，含可直接复用的知识单元：DeepSeek super agent 的 rou |
| `a975b09e7859_你需要多少钱，才能财富自由？ - 你需要多少钱，才能财富自由？` | accept | core | accept | 文件名严重误导（标题写财富自由，实为iPhone16评测视频转录），但按内容判：含可直接复用 |
| `acd10311a5bf_2026-11-18-容器化技术是怎么一步步演进到-Kuber` | accept | core | accept | 技术学习笔记，含多个可独立复用的知识单元：容器化演进主线（Docker打包→编排瓶颈→K8s |
| `acea2e2c7b52_你需要多少钱，才能财富自由？.md` | accept | core | accept | 视频转写稿但含完整可复用知识单元：财富自由公式(480X+K·n+M+H)及其推导假设(实际 |
| `b28289b3e613_【后厨重地·网关】Nginx、API网关、Ingress、Ga` | accept | core | accept | 网关主题视频的转写稿，虽语音识别错字较多（Andre X=Nginx、泡的=Pod、STU  |
| `b7d4aecca4cb_2026-11-18-为什么不能用-JSON.parse(JS` | accept | core | accept | 技术笔记含完整可复用知识单元：逐类型列出 JSON 序列化的失效表现（undefined/函 |
| `b96a4e86d668_一条视频搞懂15个主流AI热词 本视频22分钟，全程大白话，一` | accept | core | accept | 这是一份自成体系的AI术语科普讲解（技术文章整理类，默认收），含多个可直接引用的知识单元：L |
| `ca96861be52f_spacex_3min_probe.md` | accept | normal | accept | 技术演讲转录片段，含可直接复用的知识单元（spec-first 工作流、信任随 agent  |
| `d232145c2744_去看这个SpaceX AI工程师1小时的课程。 今晚别刷Net` | accept | normal | accept | 这是一份技术演讲的完整转录（含画面描述与双语字幕），主体是关于 agent 工程的可复用知识 |
| `d30c55859577_2026-11-05-给我讲讲和图片内容相关的知识.md` | accept | core | accept | 技术学习笔记，含可直接复用的知识单元：三平台触发器能力对比表（含 Coze 1 分钟时限/仅 |
| `e664b559e433_你需要多少钱，才能财富自由？.md` | accept | core | accept | 虽为视频转写且ASR错字较多，但完整保留了可直接复用的知识单元：财富自由临界值公式(480X |
| `f52f9efb0479_【后厨重地·数据库】有了MySQL，为什么还要10个库？Red` | accept | core | accept | 技术讲解视频的完整字幕+画面描述，系统讲清了各数据库为何存在（原地改=随机写、Redis缓存 |
| `f6bb88ad06a6_2026-11-18-容器化技术是怎么一步步演进到-Kuber` | accept | core | accept | 技术笔记类，含可独立复用的知识单元：Docker→Kubernetes 演进主线（打包→规模 |
| `fa628fa89d95_2026-11-17-简历写法与项目经历表达.md` | accept | core | accept | 自写学习整理笔记，含可直接复用的知识单元（简历硬性规则、项目经历STAR结构、'只写经得起面 |
| `fb9180e066a4_你需要多少钱，才能财富自由？.md` | accept | normal | accept | 含可直接复用的知识单元：财富自由测算公式(480×月开销X+单孩成本×n+健康开销M+重大投 |
| `fc7a78f88d08_2026-11-18-RAG-是什么？它解决大模型的什么问题？` | accept | core | accept | 技术学习笔记，自含完整可复用知识单元：RAG 的定义与三大痛点、Embedding 与向量数 |

## 分歧清单(主人逐条裁决)

| 文件 | 机器判定 | 质量 | AI判定(理由) | 你的裁决 |
|---|---|---|---|---|
| `26c0cbc8ec9e_「Github一周热点137期」快速看懂 DeepSeek s` | accept | reference | **partial**(仅有两条项目名称+链接+一行描述（dsh-superagent-prototype、agen) |  |
| `42b651897cb4_「Github一周热点137期」快速看懂 DeepSeek s` | reject | junk | **partial**(文件名标称「快速看懂 DeepSeek superAgent」，但正文 95% 以上是内容错) |  |
| `499aec4b58eb_「Github一周热点137期」快速看懂 DeepSeek s` | reject | junk | **partial**(仅文件头部约5行有可复用内容（dsh-superagent-prototype 与 agen) |  |
| `4b577ec72ee8_三分钟学会git和github.md` | accept | normal | **partial**(B站视频的语音转写稿，含可复用的概念性结论（版本控制的用途、GitHub私有仓库可当文档备份) |  |
| `9eba0e5331b9_「Github一周热点137期」快速看懂 DeepSeek s` | accept | reference | **reject**(视频字幕抓取稿：标题是「快速看懂 DeepSeek superAgent」，但正文 90% ) |  |
| `e329fff4f7ff_从零到一这个过程，谁都帮不了你，你只能靠你自己.md` | accept | normal | **reject**(这是外部视频的语音转写稿，通篇是博主口语化的人生感悟漫谈（从零到一、炒股类比、躺平自洽等主观) |  |

## AI 未判(当前模型不视觉/解析不了,不阻塞转正判定)

- `c142b75081c9_03d276841452819b07e75e425348b46`:unreadable 解析不了(no_text_expected):视觉识别失败(LLMResponseError

---

**转正操作**:主人在上表「你的裁决」列填 accept/partial/reject(或整体说「一致的都转正」),
执行=挑中的文件挪 `inputs/samples/_inbox/` + candidates.py --resume 重扫 + labels.csv 出骨架。
