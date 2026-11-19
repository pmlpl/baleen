"""g2_eval 评测脚本的口径正确性测试（2026-11-19）。

起点是一个口径缺陷：`predict_one` 调 `parse_file(path)` 时**没传 client**，
于是纯图片 / 扫描件 PDF 全部降级成 `no_text_expected` → 被算成「G0 确定性
拒收」计入误拒率。但生产环境配了视觉模型时这些样本是**能读的**（视觉转写 /
150dpi 栅格化 OCR）。拿评测口径的降级去算「系统会不会误拒好笔记」是错的口径
——那是评测条件缺失，不是产品能力缺失。修复前 71 条里 14 条被打到 G0，
其中 9 条就是这一条造成的。

这里不做真视觉调用（要外部 key、烧 token），只断言**接线**：
client 必须出现在传给 parse_file 的参数里。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

# backend/scripts/g2_eval.py —— 不是包内模块，按路径加载
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "g2_eval.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("g2_eval_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_predict_one_passes_client_to_parse_file(monkeypatch, tmp_path: Path) -> None:
    """parse_file 必须收到 client —— 这是视觉通道能生效的唯一前提。

    回归场景：如果以后有人「为了省 token」把 client 去掉，这条测试会红。
    """
    G = _load()
    captured: dict = {}

    sentinel_client = SimpleNamespace(model="fake-vision-model", probe=lambda: None)

    def fake_parse_file(path, *, client=None, media_sink=None):
        captured["client"] = client
        captured["path"] = path
        # 模拟「能读，但被 G1 判拒」以最短路径返回，不碰真 G1/G2
        return SimpleNamespace(
            parse_status=SimpleNamespace(value="ok"),
            blocks={"type": "doc", "content": []},
            warnings=[],
            message=None,
            meta={},
        )

    def fake_judge_g1(pr):
        return SimpleNamespace(verdict="reject", quality="junk", action="drop", reasons=["stub"], meta={})

    monkeypatch.setattr(G, "parse_file", fake_parse_file)
    monkeypatch.setattr(G, "judge_g1", fake_judge_g1)

    sample = tmp_path / "x.jpg"
    sample.write_bytes(b"\xff\xd8\xff")  # 内容无所谓，parse_file 被打桩

    G.predict_one(sample, "_inbox/x.jpg", "charter", sentinel_client, False)

    assert captured.get("client") is sentinel_client, (
        "parse_file 必须收到 client，否则图片/扫描件 PDF 会被降级成 no_text_expected"
    )


def test_g0_row_records_parse_status(monkeypatch, tmp_path: Path) -> None:
    """G0 非 ok 时 raw_json 要带 stage=G0 + parse_status（ok 口径靠这两个字段剔除）。"""
    G = _load()

    def fake_parse_file(path, *, client=None, media_sink=None):
        return SimpleNamespace(
            parse_status=SimpleNamespace(value="unsupported_format"),
            blocks={"type": "doc", "content": []},
            warnings=["w"],
            message="请另存为 .docx",
            meta={},
        )

    monkeypatch.setattr(G, "parse_file", fake_parse_file)
    sample = tmp_path / "x.doc"
    sample.write_bytes(b"stub")

    import json

    row = G.predict_one(sample, "_inbox/x.doc", "charter", SimpleNamespace(), False)
    assert row["pred_verdict"] == "reject"
    assert row["pred_action"] == "drop"
    raw = json.loads(row["raw_json"])
    assert raw["stage"] == "G0"
    assert raw["parse_status"] == "unsupported_format"
