# 待填交付块（2026-11-01 起）

> 由 `scripts/doc_backfill.py` 从 `git log` 生成，只包含**能机械提取的事实**。
> 三格 `__待填__` 必须人写——写不出来的，说明这次交付本身没想清。
>
> 填完：把块挪进 `backend/delivery/` 对应月份文件，标题 `### 待填 · ` 改成 `## `，
> 然后删掉本文件。**在本文件存在期间，doc_lint 的「交付块覆盖」WARN 不会消失**，这是故意的。

---

### 待填 · 2026-11-01 · `b712624` · feat(vision): 图片视觉理解——导入时看图转文字进流水线 + 问答回传原图(ADR 0006, api.md v13)

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M backend/app/api/qa.py`
- `M backend/app/service/imports/pipeline.py`
- `M backend/app/service/parse/router.py`
- `A backend/app/service/parse/vision.py`
- `M backend/app/service/qa/answerer.py`
- `A backend/tests/api/test_qa_image.py`
- `M backend/tests/service/parse/test_parse.py`
- `A backend/tests/service/parse/test_vision.py`
- `A docs/adr/0006-image-vision.md`
- `M docs/api.md`

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-01 · `3785b00` · feat(notes): NoteView mermaid 阅读页渲染 + DELIVERY 更新

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M backend/DELIVERY.md`
- `M frontend/src/views/NoteView.vue`

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-02 · `00e3aed` · feat(library): 三小批——笔记删除 + CSV 导入 + SVG 插图

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M backend/DELIVERY.md`
- `M backend/app/api/library.py`
- `M backend/app/repo/notes.py`
- `M backend/app/repo/sources.py`
- `A backend/app/service/parse/csv_parser.py`
- `M backend/app/service/parse/router.py`
- `M backend/app/service/qa/answerer.py`
- `M backend/app/service/qa/summarizer.py`
- `A backend/tests/api/test_note_delete.py`
- `A backend/tests/service/parse/test_csv.py`
- `M docs/api.md`
- `M frontend/src/api/notes.ts`
- …共 17 个文件

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-02 · `4cf10f6` · feat(vision): 扫描件 PDF 逐页视觉识别(ADR 0006 §6, api.md v15)

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M backend/DELIVERY.md`
- `M backend/app/service/parse/pdf_parser.py`
- `M backend/app/service/parse/router.py`
- `M backend/app/service/parse/vision.py`
- `A backend/tests/service/parse/test_vision_pdf.py`
- `M docs/adr/0006-image-vision.md`
- `M docs/api.md`

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-02 · `e83c8a0` · feat(notes): 含图笔记可编辑 + md 带图片导入(api.md v16)

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M HANDOFF.md`
- `M backend/DELIVERY.md`
- `M backend/app/api/library.py`
- `M backend/app/api/upload.py`
- `M backend/app/service/imports/pipeline.py`
- `A backend/app/service/media.py`
- `M backend/app/service/notes_edit.py`
- `M backend/app/service/parse/md_parser.py`
- `M backend/app/service/parse/router.py`
- `M backend/app/worker/worker.py`
- `M backend/tests/api/test_library.py`
- `A backend/tests/api/test_media.py`
- …共 20 个文件

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-03 · `75fd66d` · feat(eval): 候选样本自动归档(C) + bootstrap 置信区间(D) + 边界测试批

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M .env.example`
- `M .gitignore`
- `M backend/DELIVERY.md`
- `M backend/app/core/config.py`
- `A backend/app/service/eval_capture.py`
- `M backend/app/service/imports/pipeline.py`
- `M backend/app/service/notes_edit.py`
- `M backend/app/service/parse/md_parser.py`
- `M backend/tests/api/conftest.py`
- `M backend/tests/api/test_media.py`
- `M backend/tests/service/import/conftest.py`
- `M backend/tests/service/import/test_pipeline.py`
- …共 21 个文件

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-05 · `c0b5ebb` · feat: 对话附件(文本+图片)+ 导入搬至笔记库页 + 挂图不搜网 + 相关性分词修复

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M backend/app/api/qa.py`
- `M backend/app/service/qa/answerer.py`
- `M backend/tests/api/test_qa.py`
- `M backend/tests/api/test_qa_image.py`
- `M backend/tests/service/qa/test_answerer.py`
- `M docs/api.md`
- `M frontend/src/api/qa.ts`
- `M frontend/src/views/LibraryView.vue`
- `M frontend/src/views/QaView.vue`

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__

---

### 待填 · 2026-11-06 · `7a23879` · feat: 整理笔记保留出处链接 + 截断兜底 + 笔记渲染修复(v18)

**产物**（自动提取，核对一下有没有混进不该进这次提交的文件）

- `M backend/app/api/qa.py`
- `M backend/app/service/llm/client.py`
- `M backend/app/service/qa/summarizer.py`
- `M backend/tests/api/test_qa.py`
- `M backend/tests/service/llm/test_client.py`
- `A backend/tests/service/qa/test_summarizer_citations.py`
- `A backend/tests/service/qa/test_summarizer_truncation.py`
- `M docs/api.md`
- `M frontend/src/styles.css`
- `A frontend/src/utils/section.ts`
- `M frontend/src/views/NoteView.vue`

**为什么做**：__待填（一句话说清解决什么问题；写不出就是没想清）__

**自测**：__待填（跑了什么命令、多少 passed、真链路验了什么）__

**已知限制**：__待填（没做的、绕过的、下次要还的）__
