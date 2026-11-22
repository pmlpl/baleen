"""转写治本三件套测试（2026-11-22）：turbo+None 参数断言、LLM 自校正 pass（应用/降级/skipped/封顶）。"""

from __future__ import annotations

import json

import pytest

from app.service.video_extractor import (
    TRANSCRIPT_FIX_MAX_CHARS,
    TRANSCRIPT_FIX_MAX_TOKENS,
    WHISPER_MODEL,
    _load_whisper,
    correct_transcript_segments,
    transcribe_audio,
)


# ---------------------------------------------------------------- 参数断言（turbo + language=None）

def test_whisper_model_is_turbo() -> None:
    """治本批：模型名必须是 large-v3-turbo（spike 选型，small 专名错听是错字根源）。"""
    assert WHISPER_MODEL == "large-v3-turbo"


def test_transcribe_uses_auto_language(monkeypatch) -> None:
    """language=None 自动检测（zh 强制对英文视频是灾难放大器——8093 实锤）。"""
    captured: dict = {}

    class FakeModel:
        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            return iter([]), type("Info", (), {"duration": 0.0})()

    monkeypatch.setattr("app.service.video_extractor._get_whisper_model", lambda: FakeModel())
    transcribe_audio(__file__)  # 路径只要存在即可,假模型不真读
    assert captured.get("language", "MISSING") is None, "转写必须 language=None 自动检测"


def test_load_whisper_prefers_local_dir(monkeypatch, tmp_path) -> None:
    """配置了 whisper_model_dir 且目录存在 → WhisperModel 收本地目录路径（禁在线下载）。"""
    captured: dict = {}

    class FakeWhisperModel:
        def __new__(cls, model_source, device="cpu", compute_type="int8"):
            captured["model_source"] = model_source
            captured["device"] = device
            return object()

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "whisper_model_dir", str(tmp_path))  # tmp_path 存在
    _load_whisper("cpu")
    assert captured["model_source"] == str(tmp_path), "本地目录存在必须用它,不许在线下载"


def test_load_whisper_falls_back_to_hub_name(monkeypatch, tmp_path) -> None:
    """未配置 whisper_model_dir → 用 hub 模型名（现行为兜底）。"""
    captured: dict = {}

    class FakeWhisperModel:
        def __new__(cls, model_source, device="cpu", compute_type="int8"):
            captured["model_source"] = model_source
            return object()

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "whisper_model_dir", "")
    _load_whisper("cpu")
    assert captured["model_source"] == WHISPER_MODEL


# ---------------------------------------------------------------- LLM 自校正 pass

SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "过去一个月里，她向生产环境合并了 3000 个 ploy crests。"},
    {"start": 3.0, "end": 6.0, "text": "这些 lint rules 是团队的底线。"},
]


class FakeFixClient:
    """返回罐头 corrections 清单（spike v2 形态），记录收到的 messages/kwargs。"""

    def __init__(self, content: str, exc: Exception | None = None) -> None:
        self.content = content
        self.exc = exc
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.exc is not None:
            raise self.exc
        return type("R", (), {"content": self.content})()


CANNED = json.dumps(
    {
        "corrections": [
            {"evidence": "ploy crests", "fixed": "pull requests", "reason": "语境为合并代码"},
            {"evidence": "不存在的片段", "fixed": "x", "reason": "找不到"},
        ]
    },
    ensure_ascii=False,
)


def test_correction_applies_and_records() -> None:
    client = FakeFixClient(CANNED)
    segs, fix = correct_transcript_segments([dict(s) for s in SEGMENTS], client)
    assert segs[0]["text"] == "过去一个月里，她向生产环境合并了 3000 个 pull requests。"
    assert segs[1]["text"] == "这些 lint rules 是团队的底线。"
    assert fix["n_proposed"] == 2
    assert fix["n_applied"] == 1
    assert fix["corrections"][0]["evidence"] == "ploy crests"
    assert fix["corrections"][0]["fixed"] == "pull requests"
    assert fix["n_missed"] == 1 and fix["missed"][0]["why"] == "evidence-not-found"
    # prompt 形态：system=FIX_PROMPT、user 带转写文本；温度 0
    assert "转写自校正员" in client.calls[0]["messages"][0]["content"]
    assert "ploy crests" in client.calls[0]["messages"][1]["content"]
    assert client.calls[0]["temperature"] == 0.0


def test_correction_degrades_on_llm_failure() -> None:
    """LLM 挂了 → 原转写原样继续（绝不抛），留痕记 error。"""
    client = FakeFixClient("", exc=RuntimeError("网关超时"))
    segs = [dict(s) for s in SEGMENTS]
    out, fix = correct_transcript_segments(segs, client)
    assert out == segs, "降级必须用原转写"
    assert "error" in fix and "网关超时" in fix["error"]
    assert fix.get("n_applied") is None


def test_correction_degrades_on_bad_json() -> None:
    client = FakeFixClient("这不是 JSON")
    segs = [dict(s) for s in SEGMENTS]
    out, fix = correct_transcript_segments(segs, client)
    assert out == segs and "error" in fix


def test_correction_skipped_without_client() -> None:
    segs = [dict(s) for s in SEGMENTS]
    out, fix = correct_transcript_segments(segs, None)
    assert out == segs and fix == {"skipped": "no_client"}


def test_correction_skipped_empty_transcript() -> None:
    client = FakeFixClient(CANNED)
    out, fix = correct_transcript_segments([], client)
    assert fix == {"skipped": "empty_transcript"}
    assert client.calls == [], "空转写不该调 LLM"


def test_correction_input_capped_at_30k() -> None:
    """超 3 万字符掐头留尾（spike 同款），max_tokens=16000（推理网关烧 reasoning 教训）。"""
    client = FakeFixClient(CANNED)
    big = [{"start": 0.0, "end": 1.0, "text": "错听词 ploy crests " * 4000}]  # ~76k 字符
    correct_transcript_segments(big, client)
    user_msg = client.calls[0]["messages"][1]["content"]
    assert "中段截断" in user_msg
    assert len(user_msg) < TRANSCRIPT_FIX_MAX_CHARS + 500
    assert client.calls[0]["max_tokens"] == TRANSCRIPT_FIX_MAX_TOKENS == 16000


def test_correction_cross_segment_evidence_recorded_as_missed() -> None:
    """evidence 跨段（转写分段切在错误词中间）→ 记 missed 不硬改,留痕可查。"""
    content = json.dumps(
        {"corrections": [{"evidence": "底线。这些 lint", "fixed": "底线。这些 lint rules", "reason": "r"}]},
        ensure_ascii=False,
    )
    client = FakeFixClient(content)
    segs = [dict(s) for s in SEGMENTS]
    out, fix = correct_transcript_segments(segs, client)
    assert out == segs, "跨段 evidence 不许乱改"
    assert fix["n_applied"] == 0 and fix["missed"][0]["why"] == "evidence-not-found"


def test_correction_stage_logged_via_progress_cb() -> None:
    """video_log 新 stage「转写校正中…」经由 progress_cb 上报（worker 写 video_log）。"""
    client = FakeFixClient(CANNED)
    stages: list[str] = []
    correct_transcript_segments([dict(s) for s in SEGMENTS], client, progress_cb=lambda s, pct, est=None: stages.append(s))
    assert "转写校正中…" in stages


def test_correction_mutates_only_first_occurrence() -> None:
    """同一 evidence 在段内多次出现只替第一处（spike 同款防误伤）。"""
    content = json.dumps({"corrections": [{"evidence": "lint", "fixed": "LINT", "reason": "r"}]})
    client = FakeFixClient(content)
    segs = [{"start": 0.0, "end": 1.0, "text": "lint rules and lint tools"}]
    out, fix = correct_transcript_segments(segs, client)
    assert out[0]["text"] == "LINT rules and lint tools"
    assert fix["corrections"][0]["occurrences"] == 2


@pytest.mark.parametrize("missing,exc", [(True, None), (False, None)])
def test_correction_never_raises(missing: bool, exc: Exception | None) -> None:
    """任何输入形态都不抛（降级契约：导入链不能因校正挂掉）。"""
    client = FakeFixClient("no json at all" if not missing else "")
    segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
    out, fix = correct_transcript_segments(segs, client)
    assert isinstance(out, list) and isinstance(fix, dict)
