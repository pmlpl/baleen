# ADR 0002:中文文档解析栈选型

- **日期**:2026-10-28
- **状态**:已验证,建议采纳(选型验证任务交付,待确认后按 §5 实施)
- **关联**:AGENTS.md §4 "解析:pymupdf / python-docx / markdown-it / PaddleOCR——具体选型待解析验证任务定"

## 1. 背景

G0 解析闸门(确定性)要把用户丢进来的文件变成干净的文本/结构,供切块 → `bge-large-zh-v1.5` 嵌入(上限 250–400 汉字,丢一个字就少一条召回)。候选 5 库:pymupdf、pdfplumber、python-docx、markdown-it-py、PaddleOCR。

## 2. 决策

**PDF 只用 pymupdf;`.docx` 用 python-docx;`.md` 用 markdown-it-py。PaddleOCR 不进 backend 默认依赖——扫描件在 G0 被"检出即拦截"(标记待 OCR),不自动转 OCR。不引入 pdfplumber。**

即:导入管线的格式路由为

| 类型 | 处理 |
|---|---|
| `.pdf` 有文字层 | pymupdf:`get_text()` 文本流 + `find_tables()` 表格 + dict 模式做噪声过滤 |
| `.pdf` 无文字层(扫描件) | G0 拦截,入"待 OCR"队列;演示期人工用独立脚本(PaddleOCR v5 mobile)补录 |
| `.docx` | python-docx(页眉走 `section.header` 结构通道,天然不混入正文) |
| `.md` | markdown-it-py(token 流直通 block JSON) |
| `.doc` / `.pptx` / `.xlsx` / 图片 | 五库均不覆盖 → 本次验证明确为**未支持类型**,另行立项(见 §6) |

### 2.1 补充决策(G0 落地时追加,2026-10-28)

| 项 | 决策 | 理由 |
|---|---|---|
| `.txt` | **支持**,纯文本直通 | `错误堆栈.txt` 是评测集里唯一能测「无上下文报错堆栈」的样本;若归 `unsupported_format`,G0 在格式层短路,G1/G2 根本跑不到 —— **答案对了但过程没考到**。用户最常导入的也是 txt/md |
| `.txt` 编码 | **UTF-8 → GB18030 → `errors='replace'` + warnings 记一条** | 实测样本 `test.txt` 是无 BOM 的 UTF-8,按系统 GBK 读会乱码,进而被误判成垃圾 |
| 0 字节文件 | `parse_status = ok` + 空 blocks + chars=0 | **不许返回 error**。返回 error 会绕过 G1 价值闸,空文件判定就测不到了 |
| 纯图片(jpg/png/jpeg…) | **`no_text_expected`**,不用 `unsupported_format` | 图片不是"格式不认识",是"本来没文本层、该走 OCR"。下游动作完全不同:排 OCR 队列 vs 告知用户转格式。与 §5.3 扫描件判定保持一致 |
| 扫描件判定 | 用 **`最大页字符数 < 20`** 或 **有文本页占比 < 10%**,不用「总字符数/页」的平均值 | 平均值会被空白页拉低:10 页 PDF 前 3 页是封面目录、后 7 页有字,平均达标但那 3 页会被误当扫描页跳过 |
| 表格内文本的剔除 | **只在 `find_tables` 成功取到单元格文本时才从文本流剔除** | `find_tables` 会把"对齐的正文"误判成表格;无条件剔除 = 正文静默丢失且无 warning |
| 图片占位 | blocks 补 `image` 占位节点 `{"type":"image","attrs":{"alt":"[图片]","index":N}}` | docx/pptx 里有图(课件 pptx 有 231 张),静默跳过会让用户不知道那儿原来有图 |

---

## 3. 评测方法与证据

两部分:**合成件**(自造 PDF/docx/md,内容已知 → 字级准确率可精确计算;含页眉/页脚/45° 斜向水印/规则表格;另造 150dpi 纯图"扫描版")+ **真实样本**(`eval/samples/_inbox/` 30 个文件,全程只读)。探针代码在 `_scratch/parsing-probe/`(可整体删除,ADR 数字均可复现)。

### 3.1 维度一:汉字抽取准确率

| 对象 | 结果 |
|---|---|
| 合成件(精确 ground truth,字级统计) | pymupdf 100%、pdfplumber 100%、python-docx 100%、markdown-it-py(含表格)100%;段落顺序滑窗相似度全部 1.0;生僻字 喆/曌/龘/㙟 全部存活;0 乱码 |
| 真实中文 PDF《软件工程复习核心知识点提取.pdf》(18 页) | 两库规范化后字符数完全相等(7901),无丢字;人工抽检 5 行(标题/副标题/目录项/正文两行)pymupdf 全对,全角标点、「」引号原位;装饰符 ★ 被抽成独立行(无害) |
| **实质分歧** | pdfplumber 的**裸文本流**在窄列堆叠单元格区域把字符拼成"块块块"、多表区域语序交错(整体相似度 97.7%);pymupdf 原文流全程干净。对以切块/嵌入为目的的文本流,pdfplumber 这种交错是毒药 |

### 3.2 维度二:表格保真度

| 对象 | pymupdf | pdfplumber |
|---|---|---|
| 合成规则表(5×4) | 结构 ✓,原始 0.95(水印压表污染 1 格)→ 滤噪后 **1.0** | 结构 ✓,原始 0.95 → 按 matrix 滤旋转字符后 **1.0** |
| 真实中文 PDF 全 18 页检出 | 22 张 | 26 张 |
| 真实件单元格文本 | find_tables 会把顿号"、"挤到单元格末尾(`明确目标 范围 规模 系统类/、、、/型`),需后处理归位 | 单元格文本**完全正确**(顿号在原位) |

结论:pdfplumber 在"表格单元格重组"这一项上确实更强;但 pymupdf 的裸文本流没有对应缺陷,且该缺陷可用几行后处理修复(§5)。表格结构行列还原两者都合格。

### 3.3 维度三:页眉页脚/水印噪声

没有任何库内建过滤,差别在**元数据是否够用**:

| 库 | 页眉/页脚 | 斜向水印 | 过滤后残留 |
|---|---|---|---|
| pymupdf | 行级 y 坐标 + 数字掩码跨页重复即可识别 | 行级 `dir ≠ (1,0)` 直接可靠判定(6 处全中) | **0** |
| pdfplumber | 同样的跨页重复策略可识别(含页码,掩码后命中 30 行) | ⚠️ `upright` 标志对 45° 旋转**失效**(仍为 True),必须读 `char.matrix` | **0** |
| python-docx | 页眉/页脚在 `section.header/footer` 独立通道,正文天然干净 | docx 场景无此问题 | 0(结构保证) |
| markdown-it-py | 无此概念 | 无此概念 | — |
| PaddleOCR | 无概念,页眉页脚照单全收 | 水印文字全部进 OCR 输出(合成件 6 处"绝密文档"全被识别),只能事后启发式去重 | — |

### 3.4 维度四:扫描件 OCR(PaddleOCR)

- 真实 _inbox 中 3 个 PDF(2026-09 系列发票/收据扫描件)文字层字符数为 **0**——纯文本解析器在扫描件上得分为 0,OCR 分支必要性成立。
- 合成 150dpi 扫描件:PP-OCR(默认 v6 medium)**汉字召回 99.6%**。
- 真实发票扫描件人工抽检:**5/5 行全对**("增值税专用发票/单据编号:INV-20240101/开票日期…/购买方名称 某某科技有限公司/销售方名称…"含数字混排全对);插画 jpg 仅识别 3 个水印字,无误收。
- **致命短板是时延**:默认 v6 medium 在本机 CPU 上 **26–113 秒/页**;换 `PP-OCRv5_mobile` 后 **4.4–5.0 秒/页**,发票 5/5 行依旧全对,模型仅 21MB。
- Windows + Python 3.13 实测三连坑:① 安装本身可过(cp313 wheel 存在,venv 722MB);② 模型下载的默认源选择不稳,需 `PADDLE_PDX_MODEL_SOURCE=bos` 直连(本机系统代理 127.0.0.1:21882 实测已失效,设了代理反而全挂);③ 默认 `run_mode=mkldnn` 在 Windows CPU 推理直接崩(`onednn_instruction.cc` PIR `NotImplementedError`),且 paddleocr 3.7 无公开参数可关,只能 monkeypatch `get_default_run_mode → "paddle"`。

### 3.5 维度五:安装难度与体积(Windows 10.0.26300 + Python 3.13.13 + uv 0.12.2,清华源实测)

| 库 | 安装 | 耗时 | 磁盘增量 | 备注 |
|---|---|---|---|---|
| pymupdf 1.26 | ✅ 一次过 | 26s | **≈155MB**(pymupdf 101M + onnxruntime 43M,连带 pymupdf4llm/pymupdf-layout) | 最重,但一个包覆盖文本+表格+版面元数据+页面渲染 |
| pdfplumber 0.11.10 | ✅ 一次过 | 5s | ≈43MB(pdfminer + pypdfium2 + Pillow + cryptography) | — |
| python-docx 1.2.0 | ✅ 一次过 | 2s | ≈11MB(lxml) | — |
| markdown-it-py 4.2.0 | ✅ 一次过 | 1s | ≈0.4MB | — |
| paddlepaddle 3.3.1 + paddleocr 3.7.0 | ⚠️ 装得上 | ~6min | **venv 722MB** + 模型 21MB(mobile)/136MB(medium) | 三个坑见 §3.4;**Python 3.13 无需降 3.12** |

速度(真实中文 PDF 全文抽取):pymupdf **0.004s** vs pdfplumber 0.08–0.16s,差 **20–40 倍**。

## 4. 为什么是这个组合(而不是别的)

**为什么 pdfplumber 落选**:它唯一的实质优势(表格单元格顿号归位)是 pymupdf 可后处理修复的小缺陷;而它的裸文本流在窄列堆叠区产生交错字符,对嵌入检索是比顿号位移更深的伤;再加 20–40 倍的速度差和一个 43MB 依赖,不符合 §3.2 的依赖纪律。

**为什么 PaddleOCR 不进主栈**:识别质量无可挑剔(99.6%/5 行全对),但 ① 扫描件在真实样本里占比 3/30;② CPU 时延 26–113s/页(medium)会卡死导入队列,mobile 5s/页 可用但那是"将来若要接"的选型结论;③ 722MB + 模型 + 三连坑的维护成本,换来的只是对 10% 样本的处理能力。"检出即拦截"在演示场景的期望损失最小,且随时可以按 §5 的既定参数升级,不需要重构。

**为什么 pymupdf 胜出**:文本流(切块/嵌入的直接输入)实测最干净;一个包同时给出文本、表格、行级元数据(dir/y,滤噪靠它)和页面渲染(给 OCR 分支留了路);最快;Py3.13/Windows 无坑。

## 5. 实施要点(给实现者的硬注意事项)

1. **水印过滤**(pymupdf dict 模式):丢弃 `dir ≠ (1,0)` 的行;页眉/页脚用"数字掩码 → 归一化文本跨页重复(≥2 页)"判定后丢弃。已验证残留为 0。
2. **顿号归位后处理**:`find_tables` 单元格内若存在"仅含标点的行",把标点回填到前文空格处。已验证可修复。
3. **扫描件判定**:用 `总字符数/页` 低于阈值,不要用 CJK 字符数(英文 PDF 会被误判成扫描件)。
4. **若将来接 OCR**(按此参数,勿再踩坑):`PP-OCRv5_mobile_det/rec`(21MB,5s/页)+ `PADDLE_PDX_MODEL_SOURCE=bos` + 强制 `run_mode="paddle"` + 不设系统代理;作为**独立可选依赖/独立进程**,不进 backend 主依赖。
5. `.doc`(二进制)、`.pptx`、`.xlsx`、纯图片:五库均不覆盖,_inbox 实测共 11/30 个文件。需要另行立项(如 LibreOffice/COM 转换、python-pptx、openpyxl),不在本 ADR 范围内。
6. Python 3.13 全部候选库可用,**无需降 3.12**。

## 6. 遗留与后续

- [ ] 按本 ADR 在 backend 落地 G0 解析模块(等确认后开任务,另走依赖新增审批:backend 将新增 `pymupdf`、`python-docx`、`markdown-it-py` 三个直接依赖)
- [ ] 扫描件"待 OCR"队列的交互设计(拦截提示文案)
- [ ] `.doc/.pptx/.xlsx/图片` 的解析立项
- [ ] paddlepaddle 上游修复 mkldnn PIR 崩溃后,重新评估 OCR 接入成本
