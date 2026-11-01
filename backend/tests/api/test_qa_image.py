"""问答命中图片笔记 → 回传原图(ADR 0006 后半步)端点测试。

复用 test_qa 的 env + qa_setup 夹具(假检索/假 LLM);原图是 tmp_path 里写真 PNG 字节,
_load_hit_images 走真实文件读取;断言 LLM payload 的 user content 变成 OpenAI 视觉数组。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.api.test_qa import _chat, _hit, _parse_sse, _sse_lines
from tests.api.test_qa import qa_setup as qa_setup_fixture  # noqa: F401 —— 夹具注册靠这条导入


@pytest.fixture
def qa_setup(request):  # noqa: ANN001 —— getfixturevalue 避开参数名与导入名互踩(F811)
    return request.getfixturevalue("qa_setup_fixture")


# 1x1 PNG
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


async def _seed_image_note(factory: Any, tmp_path: Any, title: str, *, ext: str = ".png") -> int:
    """图片来源的笔记:source.ext=图片扩展名 + storage_path 指向真实 PNG 文件。"""
    import hashlib
    import os

    from app.repo import Repos

    blob = tmp_path / f"blob-{title}-{ext}.bin"
    blob.write_bytes(PNG_1X1)
    sha = hashlib.sha256(os.urandom(16)).hexdigest()

    async with factory() as session:
        repos = Repos(session, 1)
        source, _ = await repos.sources.create_or_get_by_sha256(
            sha256=sha,
            filename=f"{title}{ext}",
            ext=ext,
            size_bytes=blob.stat().st_size,
            storage_path=str(blob),
            parse_status="ok",
        )
        note = await repos.notes.create(
            source_id=source.id,
            title=title,
            doc={"type": "doc", "content": []},
            quality="core",
            lang="zh",
        )
        await session.commit()
        return note.id


def _last_payload_messages(transport) -> list[dict]:
    body = json.loads(transport.calls[-1]["body"])
    return body["messages"]


def test_chat_attaches_hit_image(env, qa_setup, tmp_path) -> None:  # noqa: ANN001
    note_id = env.client.portal.call(
        lambda: _seed_image_note(env.shared["factory"], env.tmp_path, "截图笔记")
    )
    qa_setup.hits = [_hit(note_id, "截图笔记", "一、图", "图里内容转写。")]
    qa_setup.responses.append((200, _chat("按图回答。")))

    resp = env.client.post("/api/qa/chat", json={"question": "这张图里是什么"})
    assert resp.status_code == 200, resp.text

    messages = _last_payload_messages(qa_setup.transports[-1])
    content = messages[-1]["content"]
    assert isinstance(content, list), "命中图片笔记时 user content 应变视觉数组"
    assert content[0]["type"] == "text" and "这张图里是什么" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    # 落库的 llm_content 仍是纯文本(重放不依赖图片;详情接口不吐该字段,直查库)
    async def check_llm_content() -> None:
        from sqlalchemy import select

        from app.models import QaMessage

        async with env.shared["factory"]() as session:
            rows = (
                (
                    await session.execute(
                        select(QaMessage).where(
                            QaMessage.session_id == resp.json()["session_id"],
                            QaMessage.role == "user",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert rows, "user 消息已落库"
            assert isinstance(rows[-1].llm_content, str)
            assert "data:image" not in (rows[-1].llm_content or "")

    env.client.portal.call(check_llm_content)


def test_chat_text_note_keeps_plain_string(env, qa_setup) -> None:  # noqa: ANN001
    note_id = env.client.portal.call(
        lambda: _seed_image_note(
            env.shared["factory"], env.tmp_path, "文本笔记", ext=".md"
        )
    )
    qa_setup.hits = [_hit(note_id, "文本笔记", "一、文", "普通正文。")]
    qa_setup.responses.append((200, _chat("答。")))

    resp = env.client.post("/api/qa/chat", json={"question": "问"})
    assert resp.status_code == 200, resp.text
    messages = _last_payload_messages(qa_setup.transports[-1])
    assert isinstance(messages[-1]["content"], str), "非图片来源命中不附图"


def test_chat_caps_at_two_images(env, qa_setup) -> None:  # noqa: ANN001
    ids = []
    for i in range(3):
        nid = env.client.portal.call(
            lambda i=i: _seed_image_note(env.shared["factory"], env.tmp_path, f"图笔记{i}")
        )
        ids.append(nid)
    qa_setup.hits = [_hit(nid, f"图笔记{i}", "一、图", "内容。") for i, nid in enumerate(ids)]
    qa_setup.responses.append((200, _chat("答。")))

    resp = env.client.post("/api/qa/chat", json={"question": "问"})
    assert resp.status_code == 200, resp.text
    content = _last_payload_messages(qa_setup.transports[-1])[-1]["content"]
    image_parts = [p for p in content if isinstance(p, dict) and p["type"] == "image_url"]
    assert len(image_parts) == 2, "最多附 2 张原图"


def test_stream_meta_reports_images_attached(env, qa_setup) -> None:  # noqa: ANN001
    note_id = env.client.portal.call(
        lambda: _seed_image_note(env.shared["factory"], env.tmp_path, "流式图笔记")
    )
    qa_setup.hits = [_hit(note_id, "流式图笔记", "一、图", "内容。")]
    qa_setup.stream_lines = _sse_lines(["答", "案"], cache_hit=0, cache_miss=100)

    resp = env.client.post("/api/qa/chat/stream", json={"question": "问"})
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    assert events["meta"]["images_attached"] == 1
    # 流式请求体里同样带上视觉数组
    content = _last_payload_messages(qa_setup.transports[-1])[-1]["content"]
    assert isinstance(content, list)
