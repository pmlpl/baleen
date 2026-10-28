# eval/DELIVERY — 评测线（Trae）交付记录

## 2026-10-28 02:16 · 线 C · split 分层（stratified-v2）+ 计数口径 + 代码去重

### 产物

- `eval/scripts/make_split.py`（新增；`--strategy stratified --seed 42`）
- `eval/scripts/_common.py`（新增；`load_existing` / `write_csv` / `sha256_of` / `build_dupe_map`）
- `eval/scripts/candidates.py`、`eval/scripts/runner.py`（改用 `_common`，删掉各写一份的实现）
- `eval/scripts/metrics.py`（加 split 版本行；非法组合与判定错误去重计数）
- `eval/labels.csv`（**只改 split 列**）、`eval/split_meta.json`（新）、`eval/report.md`（新版）

### 自测

- `python eval/scripts/make_split.py --labels eval/labels.csv --dry-run`
  → 35 条，test 14 / dev 21；test = accept 6 / partial 1 / reject 7；14 个槽位每个在 test ≥1
- `python eval/scripts/make_split.py --labels eval/labels.csv`
  → 只改 split 列；与阶段 2 备份逐字段比对，差异只有 `split` 与你手改过的 `char_count`，
    `human_*`（13 列）与 `slot` 全部无差异
  → 配对 `错误堆栈.txt` ↔ `缺陷反馈_VIP音乐播放能力缺失.md` 同在 test；
    `20260919_...pdf` ↔ canonical `20260906_...pdf` 同在 test
- `python eval/scripts/metrics.py --labels eval/labels.csv --preds eval/preds.csv --split test --out eval/report.md`
  → 14 条参与计算；误拒率 0/7 = 0.0%；误丢率 0/1 = 0.0%；非法组合 2；判定错误 2；去重后唯一错误 4
  → 准确率 12/14 = 85.7%，macro-F1 0.59
- `python eval/scripts/metrics.py --labels <human_* 全空副本> --preds eval/preds.csv --split test`
  → exit=1，输出「还差 14 条未标注，暂不计算指标」+ 未标注清单，无 traceback
- 代码去重等价性（重构前后）：
  - `dupe_of`：旧 `fill_dupe(sha256 字典)` 与新 `build_dupe_map` 输出完全一致（35 行，唯一重复对相同）
  - `candidates.csv`：逐字节一致
  - `preds.csv`：除 `latency_ms` 外 9 列 + `raw_json` 逐一比对一致；`--resume` 与全量跑结果一致
  - `labels.csv`：`make_split.py` 重写前后逐字节一致（同 seed 可复现）
  - `report.md`：除「P95 延迟」随本次墙钟变化（56ms → 52ms）外，全文一致
- 代码总量：**1197 行**（`_common 48 + candidates 419 + predictor_fake 146 + runner 194 + metrics 219 + make_split 179`），在 1200 上限内

### 已知限制

- **test 里 `topic_dialog` 只有 1 条**（《项目实施记录（一）》），误丢率 / 话题召回率的分母是 1，
  数值只能当方向看，不能当比例结论。已加规则 7 保底，但样本本身只有 2 条 topic_dialog。
- **`partial` 在 test 只有 1 条**（《实践项目指导书.docx》），且 fake predictor 判成 accept，
  partial 类 F1 = 0，把 macro-F1 从 0.89 拉到 0.59；这是模型能力问题，不是 split 问题。
- **`preds.csv` 无法逐字节 diff**：`latency_ms` 是墙钟时间，每次跑都不同。已改为「除该列外全列比对」，
  其余 9 列 + `raw_json` 逐行一致。
- **扫描件 / 手写 jpg 无法 OCR**：fake predictor 对无文本层文件只能给
  `pred_verdict=reject` + `pred_action=extract_spans`（进待 OCR 队列），这正是 2 条非法组合的来源。
  产品口径若定为「待 OCR」就与 D5 的四条合法映射冲突（合法映射里 reject 只能接 topic_dialog / drop）。
- `.doc`（OLE2）、`.xlsx` 按 AGENTS.md 解析栈在 G0 直接拒收，不参与内容判定；
  但 `labels.csv` 里这两类的人工判定是按内容给的（如《霸道总裁爱上我.doc》= accept），
  所以指标里会稳定出现「误拒」，这不是预测器判错，而是格式能力缺口。
- 质量档次排序 `core > normal > reference > junk` 是我的口径假设（`reference` 视为查阅型资料，
  低于 `normal`）。若认可的不是这个序，「低两档」清单会变。

### 需要确认的问题

1. **非法组合与 `no_text_expected` 的口径冲突**（第二次提）：D5 的四条合法映射没有「无文本层、待 OCR」的出口。
   建议二选一并写进 TASK.md：① 合法映射补 `reject→extract_spans`（语义：待 OCR 抽取后再判）；
   ② 扫描件/图片一律判 `partial` + `extract_spans`。我目前按 ① 的语义输出，但没改 D5。
2. **误丢率只覆盖 `topic_dialog`**：若以后 `partial` 也算「可再生产」，分母口径要一起改。
3. **质量低两档的档次序**（见上）是否认可；不认可的话我改 `QUALITY_RANK` 一个常量即可。

### 踩到的坑（记一句）

最初把「无文本层」当成普通拒绝处理，于是扫描件/图片落到 `reject + drop`；
但 AGENTS.md 决策 9 是「默认收 + 标质量」，扫描件应进待处理队列而不是丢弃，
所以改成 `reject + extract_spans`——结果就撞上了 D5 的合法映射表。**根因是合法映射表缺少
「不可判但别丢」这一类动作**，不是预测器笔误。
