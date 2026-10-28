#!/usr/bin/env python3
"""规则桩预测器：只做 G0 的确定性判断 + 关键词命中，**不调用任何 LLM**。

它不是产品闸门的实现，只是给 `runner.py --predictor fake` 提供一条可跑通的基线，
用来先验证评测流水线本身。预测结果的对错由 `metrics.py` 去算。

输入：文件路径 + `candidates.py` 抽出的特征 + 去重信息（dupe_of）
输出：pred_verdict / pred_quality / pred_note_type / pred_score / pred_action

`error` 只留给真正的异常；G0 的正常拒收原因写进 `raw.reason`。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
UNSUPPORTED_EXTS = {".xlsx", ".xls"}

TRACE_RE = re.compile(
    r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+|^\s*at .+\(.+\)",
    re.M | re.I,
)
SPEAKER_RE = re.compile(r"^\s*[\w\u4e00-\u9fff][\w\u4e00-\u9fff ]{0,11}\s*[:：]\s*\S", re.M)

FIX_WORDS = ("根因", "修复", "解决", "改为", "改成", "规避", "锁到", "绕开", "定位")
ADMIN_WORDS = ("姓名", "学号", "签名", "盖章", "审批", "承诺书", "巡访", "班级", "填表", "备注")
PROCESS_WORDS = ("实训", "汇报", "实施记录", "进度", "教学安排", "指导团队", "评分要求", "流程")
CHAT_WORDS = ("群聊", "聊天记录", "微信", "QQ", "回复：")
DOC_WORDS = ("安装", "部署", "目录结构", "快速开始", "配置", "接口", "API")
TEXTISH_EXTS = {".md", ".markdown", ".txt", ".text", ".log", ".html", ".htm"}

def _hits(words: tuple[str, ...], text: str) -> int:
    return sum(1 for w in words if w in text)

def predict(path: Path, features: dict[str, Any], dupe_of: str = "") -> dict[str, Any]:
    """返回一行预测结果。规则顺序即优先级，先命中先退出。"""
    ext = path.suffix.lower()
    raw_text = features.get("text")
    text = raw_text or ""
    n_chars = len(text)
    heads = int(features.get("heading_count", 0))
    tables = int(features.get("table_count", 0))
    code = bool(features.get("has_code_block", False))
    pages = int(features.get("page_count", 0))
    images = int(features.get("image_count", 0))

    def out(
        verdict: str,
        quality: str,
        note_type: str,
        score: int,
        action: str,
        rule: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return {
            "pred_verdict": verdict,
            "pred_quality": quality,
            "pred_note_type": note_type,
            "pred_score": score,
            "pred_action": action,
            "error": "",
            "raw": {
                "rule": rule,
                "reason": reason,
                "dupe_of": dupe_of,
                "char_count": -1 if raw_text is None else n_chars,
                "heading_count": heads,
                "table_count": tables,
                "has_code_block": code,
            },
        }

    # --- G0：确定性判断 ---------------------------------------------------
    if dupe_of:
        return out("reject", "junk", "other", 1, "drop", "g0_duplicate", f"与 {dupe_of} 字节相同")

    if ext in UNSUPPORTED_EXTS:
        return out("reject", "junk", "other", 1, "drop", "g0_unsupported",
                   "该格式暂不支持（见 AGENTS.md 解析栈）")

    if raw_text is None:
        if ext in IMAGE_EXTS or pages > 0:
            # 无文本层：不判死活，进待 OCR 队列
            return out("reject", "junk", "other", 1, "extract_spans", "g0_no_text",
                       "no_text_expected：无文本层，需 OCR")
        return out("reject", "junk", "other", 1, "drop", "g0_unreadable",
                   "无法抽取文本（.doc 未支持，请转 .docx）")

    if n_chars == 0:
        return out("reject", "junk", "other", 1, "drop", "g0_empty", "空文本")

    if n_chars < 50 and (pages > 0 or images > 0):
        return out("reject", "junk", "other", 1, "extract_spans", "g0_no_text",
                   "no_text_expected：可抽取文本 < 50 字，需 OCR")

    if n_chars < 50:
        return out("reject", "junk", "other", 1, "drop", "g0_too_short", "文本过短")

    # --- G1：关键词命中 ---------------------------------------------------
    fix_hits = _hits(FIX_WORDS, text)

    if TRACE_RE.search(text) and fix_hits == 0:
        return out("reject", "junk", "log", 1, "drop", "g1_bare_traceback",
                   "只有堆栈，无触发条件与结论")

    if ("复现" in text or "根因" in text) and fix_hits >= 1:
        return out("accept", "core", "case", 4, "index", "g1_bug_report",
                   "含复现/根因与修复结论")

    if _hits(ADMIN_WORDS, text) >= 3 and fix_hits == 0 and n_chars < 3000:
        return out("reject", "junk", "other", 1, "drop", "g1_blank_form", "空白表单/行政文书")

    if _hits(PROCESS_WORDS, text) >= 2 and fix_hits == 0 and n_chars < 5000:
        # 流程汇报，无技术结论：不进索引，转成待追问话题
        return out("reject", "junk", "procedure", 2, "topic_dialog", "g1_process_log",
                   "流程汇报，无技术结论")

    speaker_lines = len(SPEAKER_RE.findall(text))
    all_lines = max(1, sum(1 for line in text.splitlines() if line.strip()))
    if _hits(CHAT_WORDS, text) >= 1 and speaker_lines >= 5 and speaker_lines / all_lines >= 0.3:
        return out("partial", "normal", "chat", 3, "topic_dialog", "g1_chat_export",
                   "聊天记录导出（发言轮次占比高）")

    # --- 结构信号：收，按结构定质量 ---------------------------------------
    readme_like = ext in TEXTISH_EXTS and (
        "readme" in path.name.lower() or _hits(DOC_WORDS, text) >= 4
    )
    if readme_like:
        return out("accept", "reference", "reference_data", 4, "index", "g1_readme_like",
                   "说明/部署类文档")

    if ext == ".pdf":
        return out("accept", "reference", "reference_data", 4, "index", "g1_pdf_text",
                   "有文本层的 PDF")

    if heads >= 3 or tables >= 2 or code:
        quality = "core" if (code or tables >= 2 or heads >= 5) else "normal"
        return out("accept", quality, "concept", 4, "index", "g1_structured",
                   "有标题/表格/代码结构")

    # 单用户默认收（AGENTS.md 决策 9）
    return out("accept", "normal", "concept", 3, "index", "default_accept", "无明显风险，默认收")
