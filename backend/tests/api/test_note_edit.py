"""PUT /api/notes/{id} 用户自编辑端点测试。

- 嵌入/token 计数都 monkeypatch(不加载 bge):embed_passage 换确定性假向量,
  notes_edit.count_tokens 换字数兜底——端点是调用时惰性导入,monkeypatch 生效;
- 隔离与 tests/api 同款:conftest env 外层事务回滚,种子带唯一标记。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.service.parse import blocks

MARKER = "note-edit-test"


def _fake_token_counter(text: str) -> int:
    import re

    return len(re.sub(r"\s+", "", text))


@pytest.fixture
def fake_embed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """embed_passage / count_tokens 换假货,记录调用次数。"""
    calls = {"embed": 0}
    monkeypatch.setattr(
        "app.service.notes_edit.count_tokens", _fake_token_counter, raising=True
    )

    def fake_embed_passage(texts: list[str]) -> list[list[float]]:
        calls["embed"] += 1
        # halfvec(1024) 列严格校验维度,假向量也必须 1024 维
        return [[float(len(t) % 7 + 1)] * 1024 for t in texts]

    monkeypatch.setattr("app.service.embed.embed_passage", fake_embed_passage, raising=True)
    return calls


async def _seed_note(factory: Any, *, title: str, doc: dict | None = None) -> int:
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        note = await repos.notes.create(
            source_id=None,
            title=title,
            doc=doc
            or {
                "type": "doc",
                "content": [
                    blocks.heading(1, MARKER + " 原标题"),
                    blocks.paragraph_from_text("原始正文。"),
                ],
            },
            quality="core",
            lang="zh",
        )
        await session.commit()
        return note.id


async def _chunk_count(factory: Any, note_id: int) -> tuple[int, int, int]:
    from sqlalchemy import func, select

    from app.models import Embedding
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        rows = await repos.chunks.get_by_note(note_id)
        parents = sum(1 for r in rows if r.is_parent)
        chunk_ids = [r.id for r in rows] or [0]
        emb = await session.execute(
            select(func.count()).select_from(Embedding).where(Embedding.chunk_id.in_(chunk_ids))
        )
        return parents, len(rows) - parents, emb.scalar_one()


def test_get_detail_markdown_now_serialized(env: SimpleNamespace) -> None:
    """GET /notes/{id} 的 markdown 字段从「恒 null」变成 doc 现场序列化。"""
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 序列化")
    )
    body = env.client.get(f"/api/notes/{note_id}").json()
    assert body["markdown"], "markdown 不再是 null"
    assert "# " in body["markdown"] and "原始正文。" in body["markdown"]


def test_edit_title_only(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 旧题")
    )
    before = env.client.portal.call(lambda: _chunk_count(env.shared["factory"], note_id))
    resp = env.client.put(f"/api/notes/{note_id}", json={"title": MARKER + " 新题"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title_changed"] is True
    assert body["content_changed"] is False
    after = env.client.portal.call(lambda: _chunk_count(env.shared["factory"], note_id))
    assert before == after, "只改标题不动切块"
    detail = env.client.get(f"/api/notes/{note_id}").json()
    assert detail["title"] == MARKER + " 新题"


def test_edit_markdown_rebuilds_index(env: SimpleNamespace, fake_embed: dict[str, Any]) -> None:
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 重建")
    )
    md = (
        f"# {MARKER} 新结构\n\n"
        "第一段,写得足够长一点,凑够切块的最小字数要求,好让切块器正常工作不出警告。\n\n"
        "第二段,同样要写长一点才行,否则块太碎,这也是笔记内容本身的常态。\n\n"
        "## 小节\n\n小节正文,也要写够长度,保证切块器给它一个稳定的分块,不至于太碎。\n"
    )
    resp = env.client.put(f"/api/notes/{note_id}", json={"markdown": md})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content_changed"] is True
    assert body["chunks_parents"] >= 1 and body["chunks_children"] >= 1
    assert body["embeddings"] == body["chunks_children"], "父块不 embed(ADR 0003)"
    parents, children, embeddings = env.client.portal.call(
        lambda: _chunk_count(env.shared["factory"], note_id)
    )
    assert (parents, children, embeddings) == (
        body["chunks_parents"], body["chunks_children"], body["embeddings"]
    )
    assert fake_embed["embed"] == 1
    detail = env.client.get(f"/api/notes/{note_id}").json()
    assert MARKER + " 新结构" in detail["markdown"]
    assert any("小节" in (p.get("heading_path") or "") for p in _flat(detail))


def _flat(detail: dict) -> list[dict]:
    return detail["parents"]


def test_edit_same_markdown_is_noop(env: SimpleNamespace, fake_embed: dict[str, Any]) -> None:
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 幂等")
    )
    md = f"# {MARKER} 稳定结构\n\n唯一一段正文,长度适中,保持两次完全一致来验证 no-op 语义。\n"
    first = env.client.put(f"/api/notes/{note_id}", json={"markdown": md})
    assert first.status_code == 200 and first.json()["content_changed"] is True
    calls_after_first = fake_embed["embed"]
    second = env.client.put(f"/api/notes/{note_id}", json={"markdown": md})
    assert second.status_code == 200
    assert second.json()["content_changed"] is False
    assert second.json()["embeddings"] == 0
    assert fake_embed["embed"] == calls_after_first, "同内容保存不重嵌"


def test_edit_image_note_content_rejected(env: SimpleNamespace) -> None:
    doc = {
        "type": "doc",
        "content": [
            blocks.paragraph_from_text("带图的笔记。"),
            {"type": "image", "attrs": {"alt": "架构图", "index": 1}},
        ],
    }
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 有图", doc=doc)
    )
    resp = env.client.put(f"/api/notes/{note_id}", json={"markdown": "# 想改内容"})
    assert resp.status_code == 400
    assert "图片" in resp.json()["detail"]
    # 只改标题放行
    ok = env.client.put(f"/api/notes/{note_id}", json={"title": MARKER + " 有图·改题"})
    assert ok.status_code == 200 and ok.json()["title_changed"] is True


def test_edit_validation(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 校验")
    )
    assert env.client.put(f"/api/notes/{note_id}", json={}).status_code == 422
    assert env.client.put(f"/api/notes/{note_id}", json={"markdown": "   "}).status_code == 422
    assert env.client.put(f"/api/notes/{note_id}", json={"title": "  "}).status_code == 422
    assert env.client.put("/api/notes/999999", json={"title": "x"}).status_code == 404
