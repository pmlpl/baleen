"""视频关键帧 sidecar → pipeline 接线测试（阶段 2）。

验证：
1. G0 读到 manifest「<md>.frames.json」→ `baleen-image:N` 还原成 image 块（带 media key）
2. organizer 收到 video_images（走视频多模态分支）
3. 没有 sidecar 的普通 md 不受影响（视频逻辑是旁路）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from app.service import media as media_module
from app.service.imports import run_import_async
from app.service.imports.pipeline import VIDEO_FRAMES_SUFFIX, _load_video_frames
from app.service.llm import ChatResult

_PARAGRAPH = (
    "讲者认为 AI 更适合当结对编程伙伴，而不是替代写代码的工具。"
    "他提到很多人把 AI 用得到处都是，却只是在追求提交 PR 的数量。"
    "真正的问题是如何信任 agent，这需要验证机制和高质量的技能定义。"
)


def _store_test_frame(media_root: Path, color: tuple[int, int, int]) -> str:
    """按 store_media 的布局往测试 media_root 落一张图，返回 sha256 key。"""
    img_path = media_root.parent / f"tmp_{color[0]}_{color[1]}.jpg"
    Image.new("RGB", (24, 24), color).save(img_path)
    data = img_path.read_bytes()
    key = hashlib.sha256(data).hexdigest()
    target = media_root / key[:2] / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return key


class _CaptureLLM:
    """记录 organizer 收到的 video_images（通过检查 user content 是否为多模态 list 判断）。"""

    model = "fake-capture-llm"

    def __init__(self) -> None:
        self.organizer_calls: list[dict] = []

    def chat(self, messages, **kwargs) -> ChatResult:
        system = messages[0]["content"] if messages else ""
        if "笔记编辑助手" in system:
            user_content = messages[1]["content"]
            is_multimodal = isinstance(user_content, list)
            text = user_content[0]["text"] if is_multimodal else str(user_content)
            self.organizer_calls.append(
                {
                    "is_multimodal": is_multimodal,
                    "has_video_rules": "视频笔记专属规则" in system,
                    "text": text,
                    "image_count": sum(
                        1 for p in (user_content if is_multimodal else []) if p.get("type") == "image_url"
                    ),
                }
            )
            content = json.dumps(
                {
                    "organizable": True,
                    "markdown": (
                        "# 信任智能体\n\n## 讲者观点\n\n" + _PARAGRAPH + "\n\n"
                        "![[0:40] 讲者画面](baleen-image:0)\n\n"
                        "## 白板\n\n白板展示了 trust 曲线。\n\n"
                        "![[1:46] 白板](baleen-image:1)"
                    ),
                    "quality": "core",
                    "note_type": "concept",
                    "reasons": ["融合了语音与画面"],
                },
                ensure_ascii=False,
            )
        else:
            content = "{}"
        return ChatResult(content=content, model=self.model, total_tokens=10)


@pytest.fixture
def isolated_media(tmp_path, monkeypatch):
    """把 media 目录指到 tmp（不污染真实 storage/media）。"""
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(media_module, "MEDIA_ROOT", root)
    return root


def test_load_video_frames_reads_sidecar(tmp_path: Path) -> None:
    """sidecar 读取：有则返回 list；无/坏 JSON 返回 None。"""
    md = tmp_path / "v.md"
    md.write_text("# v", encoding="utf-8")
    assert _load_video_frames(md) is None

    sidecar = md.with_name(md.name + VIDEO_FRAMES_SUFFIX)
    sidecar.write_text(json.dumps([{"index": 0, "media_key": "a" * 64}]), encoding="utf-8")
    assert _load_video_frames(md) == [{"index": 0, "media_key": "a" * 64}]

    sidecar.write_text("not json", encoding="utf-8")
    assert _load_video_frames(md) is None

    sidecar.unlink()
    sidecar.write_text('{"not": "list"}', encoding="utf-8")
    assert _load_video_frames(md) is None


def test_video_sidecar_restores_image_blocks_and_feeds_organizer(
    tmp_path, db_scope, fake_embed, fake_tokens, isolated_media
) -> None:
    """完整链路：md 带 baleen-image:N + sidecar → G0 还原 image 块 + organizer 多模态。"""
    media_root = isolated_media
    key0 = _store_test_frame(media_root, (200, 0, 0))
    key1 = _store_test_frame(media_root, (0, 0, 200))

    md_text = (
        "# 视频标题\n\n"
        + _PARAGRAPH
        + "\n\n![[0:40] 讲者](baleen-image:0)\n\n"
        + _PARAGRAPH
        + "\n\n![[1:46] 白板](baleen-image:1)\n"
    )
    md = tmp_path / "video.md"
    md.write_text(md_text, encoding="utf-8")
    manifest = [
        {"index": 0, "media_key": key0, "alt": "讲者", "text_layer": "字幕文字", "timestamp": 40.0},
        {"index": 1, "media_key": key1, "alt": "白板", "text_layer": "trust 曲线", "timestamp": 106.0},
    ]
    md.with_name(md.name + VIDEO_FRAMES_SUFFIX).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    llm = _CaptureLLM()

    async def case():
        async with db_scope() as factory:
            result = await run_import_async(
                md,
                session_factory=factory,
                embed_func=fake_embed,
                token_counter=fake_tokens,
                client=llm,
            )
            assert result.parse_status == "ok"
            assert result.note_id is not None

            # organizer 收到视频多模态（2 张图 + 视频规则）
            assert len(llm.organizer_calls) == 1
            call = llm.organizer_calls[0]
            assert call["has_video_rules"] is True
            assert call["is_multimodal"] is True
            assert call["image_count"] == 2
            assert "baleen-image:0" in call["text"]  # 标记→画面对照表

            # 落库的笔记 doc：图片没丢（organizer 重写后仍按 sidecar 还原）
            from app.repo import Repos
            from app.service.notes_edit import _iter_images

            async with factory() as session:
                note = await Repos(session, 1).notes.get(result.note_id)
                imgs = _iter_images((note.doc or {}).get("content") or [])
                assert len(imgs) == 2
                assert imgs[0]["attrs"]["media"] == key0
                assert imgs[1]["attrs"]["media"] == key1

    asyncio.run(case())


def test_plain_md_without_sidecar_unaffected(
    tmp_path, db_scope, fake_embed, fake_tokens
) -> None:
    """普通 md（无 sidecar）走原路径：organizer 非多模态、无视频规则。"""
    body = "\n\n".join([_PARAGRAPH] * 3)
    md = tmp_path / "plain.md"
    md.write_text("# 标题\n\n" + body, encoding="utf-8")
    llm = _CaptureLLM()

    async def case():
        async with db_scope() as factory:
            result = await run_import_async(
                md,
                session_factory=factory,
                embed_func=fake_embed,
                token_counter=fake_tokens,
                client=llm,
            )
            assert result.note_id is not None
            assert llm.organizer_calls[0]["is_multimodal"] is False
            assert llm.organizer_calls[0]["has_video_rules"] is False

    asyncio.run(case())
