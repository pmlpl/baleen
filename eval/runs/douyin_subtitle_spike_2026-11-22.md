# 彩蛋探测报告：抖音平台字幕白嫖（2026-11-22）

> 执行：探测会话（sess_b1c3cc6c，中途模型层空回复反复失败被拦停一次，恢复后补全四路采样+播放器验证；期中版由主会话代写，本版为终版合并）。工件=`backend/tmp/douyin_subtitle_probe/`

## 结论：**不可得（匿名网页路径）→ 不值得接入。** 白嫖字节 ASR 的假设关闭

数据支撑：**366 个匿名网页样本 0 条带平台 CC 字幕**（实验批 10/10 + 推荐 feed 330 + 作者页 18），播放器字幕菜单实测只有「不开启」。真正值得做的转写提升在引擎层，不在平台字幕（见 §5）。

## 1. 探测方法与样本覆盖

复用生产同款路径（系统 Edge 匿名打开视频页，截 `aweme/v1/web/aweme/detail` 完整响应，88-135KB/条），四路采样：

| 采样面 | 样本 | `is_subtitled` | `cla_info`/`caption_infos` | `video_text` |
| --- | --- | --- | --- | --- |
| 实验批 10 条详情接口（7532-7541，`detail_intercept.py` 批量） | 10 | 0×3 / null×7 | 0 命中 | `[]` |
| 首页推荐 feed（v2/module/feed）滚动（`search_feed_probe.py`） | 330 | 0×20 / 字段缺省×310 | 0 命中 | — |
| 作者主页视频列表（大白讲技术 18 条，`user_post_probe.py`） | 18 | 字段缺省 | 0 命中 | `[]` |
| 移动端 share 页 SSR（`share_page_probe.py`，纯 urllib） | 1 | — | `video_layout: null` 空壳 | — |

key 名递归扫（caption_info/cla_info/video_text/webvtt，大小写均试）+ 原始 JSON 全文 grep 双重确认。

**播放器实测**（`cc_button_probe.py`/`cc_click_probe.py`，7532+7533 双样本）：控制栏「字幕」按钮（xgplayer `texttrack` 控件）恒渲染，点开菜单**只有一个选项「不开启」**——没有任何字幕轨可开；点击前后均无字幕类网络请求、无字幕 DOM。

## 2. 证据判读

1. 这批知识类视频的"字幕"是创作者**烧进画面的像素**（剪映自动字幕），详情接口不携带文本层。视觉层直证：8093 笔记的帧转录里有烧字双语字幕行（"you know whenever we cut a new release now / 你知道现在每当我们剪辑新版本时"）。
2. `caption_start/end` 是**描述文案里话题标签的字符偏移**（#话题 在文案中的位置），不是字幕时间轴——搜索时虚晃一枪，取证后排除。
3. 字段形态理论上存在：公开逆向生态（TikHub 文档、「抖音视频文本导出」类 Chrome 插件）确认 `video.cla_info.caption_infos[].url_list` → `.webvtt` 是平台 CC 的载体，`label_type` 区分 asr/人工。但实测连一个 `is_subtitled=1` 的样本都没遇到，**webvtt 匿名可下载性未能端到端验证**。另一个侧面印证：知名下载库 Douyin_TikTok_Download_API 也不抓字幕，直接用 FunASR 本地转写。
4. 未覆盖面（诚实声明）：匿名搜索页有登录墙；APP 内/登录态下官方号、影视类内容可能带 CC；热搜视频 8 条批量详情全拦不到（连续访问触发风控或直播/图集形态）。baleen 管线是匿名零配置，这些面触达不了。

## 3. 匹配风险评估（若未来接入）

- **无 B 站分 P 错配风险**：`cla_info` 与 `aweme_id` 同在一个 JSON 对象内绑定，per-item 数据，不存在 B 站「选错字幕轨/分 P」式错配。B 站教训（v20-22）不适用于此结构。
- 真正要处理：语言选择（`lang`）、`label_type` 过滤、字幕时长与视频时长对账、webvtt 时间戳→段结构转换。
- **fallback 策略**（仅方案，不实现）：`is_subtitled==1` 才尝试；字幕缺失/时长偏差 >5%/webvtt 拉取失败 → 回退 whisper。插入点在 `douyin_extractor.fetch_douyin_video` 的 `captured["data"]` 之后（完整 `aweme_detail` 已在手，约 +40 行），`_content_from_media` 前旁路 `transcribe_audio`。

## 4. 对治本路线的影响

白嫖路径关闭 → **转写质量治本只剩引擎升级路线**（spike 会话实测中：small vs large-v3 vs SenseVoice + LLM 自校正，报告将出 `eval/runs/transcribe_spike_2026-11-22.md`）。

探测顺带发现的独立问题（比白嫖字幕大一档的免费改进点）：8093（17 分钟英文演讲）whisper small 被强制 `language="zh"`（`video_extractor.py:325`），输出 16.3K 字符**大面积破碎**——organizer 修掉大部分后终审才只逮到 3 条错字：

- 专名错字 ≥20 处：`graphbot`/`grohbot`/`gropot`(GrokBot)、`space xsi`(SpaceX)、`brettaylor`(Bret Taylor)、`ploy crest`(pull requests)、`election apps`/`an electronic`(Electron)、`lit rules`(lint)、`slough`(slop)、`jeng`(jank)、`escape patch`(escape hatch)、`invariance`(invariants)、`overloop`(outer loop)、`techdeb`、`automurge`…
- 全文断词粘连：`software factorybut`、`in my sleepbut`、`ban99%`、`isPotato,osunonesloren` 等，空格丢失遍布。

中文侧（`lang_census.py` 语言普查）：9 条中文视频错字与已知「7/10 有错字」一致（"AA工具"(AI)、"附在均衡"(负载均衡)、"Azint"(Agent)、"分水灵"(分水岭)、"倚苟"+简繁混杂），继续走 quality 质检闭环。

**建议单独立项**：`transcribe_audio` 放开语言自动检测（whisper `language=None`，或按标题/简介猜语言）——一行级改动，预期消灭英文演讲类全崩；中文不受影响。此项与 transcribe_spike 的选型正交。

## 5. 建议

**不值得接入**（匿名管线无可接入之物）。转写治本走 transcribe_spike 的选型结论；语言自动检测作为独立小改动另行拍板。

## 附录：实验批 10 条覆盖面明细

| source | video_id | is_subtitled | cla 字段 | 时长 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 7532 | 7710186307976966208 | 0 | 无 | 1021s | 英文演讲，whisper 全崩样本 |
| 7533 | 7710295362812071168 | null | 无 | 342s | PostgreSQL 系列 |
| 7534 | 7700921390798523675 | null | 无 | 628s | WinClaw 实测 |
| 7535 | 7705712423516310824 | null | 无 | 214s | 系统设计 |
| 7536 | 7707457197550161215 | null | 无 | 1046s | 异步 Agent |
| 7537 | 7702436256281611555 | null | 无 | 1153s | 财务思维播客 |
| 7538 | 7702444393512406323 | null | 无 | 676s | 低工资高效率 |
| 7539 | 7707913718885721935 | 0 | 无 | 590s | 打野教学 |
| 7540 | 7707211680812329707 | 0 | 无 | 545s | 暃打法 |
| 7541 | 7708304100508601615 | null | 无 | 785s | FDE 访谈 |

探针脚本清单（`backend/tmp/douyin_subtitle_probe/`）：`detail_intercept.py`（批量详情拦截，含裸 id）、`search_feed_probe.py`（feed 大样本，结构无关 aweme 扫描）、`user_post_probe.py`（作者页）、`cc_button_probe.py`/`cc_click_probe.py`/`cc_dom_debug.py`（播放器字幕菜单三步定位）、`share_page_probe.py`（移动端 SSR）、`hot_search_probe.py`（热搜挖 id）、`db_probe.py`/`dump_whisper_8093.py`/`lang_census.py`（只读 DB 侧）。原始响应 detail_*.json ×13、feed__home/、user_*/、hot_search.json、coverage_summary.tsv 全部留档。全程未入队导入、未改生产代码、DB 仅 SELECT。
