"""GET /api/media/{key} 端点测试(v16):md 导入的图片按内容寻址取回。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.service.media import store_media

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


def test_media_endpoint_serves_png(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.media.MEDIA_ROOT", env.tmp_path / "media")
    key = store_media(PNG_BYTES)
    resp = env.client.get(f"/api/media/{key}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == PNG_BYTES


def test_media_endpoint_404(env: SimpleNamespace) -> None:
    assert env.client.get(f"/api/media/{'a' * 64}").status_code == 404
    assert env.client.get("/api/media/../secret").status_code == 404


# ---------------------------------------------------------------- 边界批(v17 前置)


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


async def _seed_note_with_doc(factory, doc: dict) -> int:
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        note = await repos.notes.create(source_id=None, title="media 语义测试", doc=doc)
        await session.commit()
        return note.id


def test_media_survives_note_delete_by_design(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """删笔记不删 media 字节:内容寻址存储,同 key 可能被其他笔记引用。

    刻意语义(与 git 对象库同理),不是泄漏;GC 是将来的独立增量。
    钉住行为,防止有人「顺手修复」破坏共享引用。
    """
    from app.service.media import media_path, store_media

    monkeypatch.setattr("app.service.media.MEDIA_ROOT", env.tmp_path / "media")
    key = store_media(PNG_1X1)
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "带媒体笔记。"}]},
            {"type": "image", "attrs": {"alt": "图", "media": key}},
        ],
    }
    note_id = env.client.portal.call(lambda: _seed_note_with_doc(env.shared["factory"], doc))
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 204
    path = media_path(key)
    assert path is not None and path.is_file(), "media 字节按设计留存"


def test_same_md_different_attachment_hits_dedupe(env) -> None:
    """同 md 二次导入(不同附件)命中 sha 去重:不再处理。

    dedupe 以文档内容为准——md 没变就没变;第二次带的图是冗余上传,静默忽略。
    """
    md = b"# dedupe and attachments\n\n![x](pic.png)\n\n" + b"y" * 200
    first = env.client.post(
        "/api/sources",
        files={
            "file": ("a.md", md, "text/markdown"),
            "attachments": ("pic.png", PNG_1X1, "image/png"),
        },
        params={"no_llm": True},
    )
    assert first.status_code == 201, first.text
    second = env.client.post(
        "/api/sources",
        files={
            "file": ("a.md", md, "text/markdown"),
            "attachments": ("other.png", PNG_1X1, "image/png"),
        },
        params={"no_llm": True},
    )
    assert second.status_code == 200, "dedupe 命中返回 200"
    # v24 语义修正:deduped=True 仅在 job 真完成过(done);此处 job 还是 queued,
    # 内容级去重体现在「同 sha → 同 source」上
    assert second.json()["deduped"] is False
    assert first.json()["source_id"] == second.json()["source_id"]
