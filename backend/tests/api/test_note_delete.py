"""DELETE /api/notes/{id} 删除端点测试。

- 真删除口径:chunks/embeddings 由 DB FK 级联清,source 孤儿时连行带 blob 文件一起删;
- 种子走 repo(chunks CHECK 要求 children 挂 parent_id,不能裸 SQL);
- 隔离与 tests/api 同款:conftest env 外层事务回滚,blob 文件放 tmp_path。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.models import Embedding as EmbeddingRow
from app.service.chunk import Chunk, ChunkResult

MARKER = "note-delete-test"


async def _seed_note(factory: Any, *, title: str, source_id: int | None = None) -> int:
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        note = await repos.notes.create(
            source_id=source_id,
            title=title,
            doc={"type": "doc", "content": [{"type": "p", "text": MARKER + " 正文。"}]},
        )
        await session.commit()
        return note.id


async def _seed_chunks_and_embedding(factory: Any, note_id: int) -> None:
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        result = ChunkResult(
            chunks=[
                Chunk(
                    text=MARKER + " 父块",
                    heading_path="P",
                    ord=0,
                    is_parent=True,
                    token_count=10,
                    section_idx=0,
                ),
                Chunk(
                    text=MARKER + " 子块",
                    heading_path="P",
                    ord=1,
                    is_parent=False,
                    token_count=8,
                    section_idx=0,
                ),
            ],
            sections=[],
            metrics={},
        )
        children = await repos.chunks.bulk_insert(note_id, result)
        for _chunk, row in children:
            session.add(
                EmbeddingRow(chunk_id=row.id, model="fake-model", dim=1024, vec=[0.5] * 1024)
            )
        await session.commit()


async def _seed_source_with_blob(factory: Any, blob: Path) -> int:
    from app.repo import Repos

    blob.write_text("原始文件内容", encoding="utf-8")
    async with factory() as session:
        repos = Repos(session, 1)
        source, _created = await repos.sources.create_or_get_by_sha256(
            sha256=f"{MARKER}-{blob.name}",
            filename=blob.name,
            ext=".txt",
            size_bytes=blob.stat().st_size,
            storage_path=str(blob),
        )
        await session.commit()
        return source.id


async def _counts(factory: Any, note_id: int) -> tuple[int, int]:
    from sqlalchemy import func

    from app.models import Chunk, Embedding

    async with factory() as session:
        chunks = int(
            await session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.note_id == note_id)
            )
            or 0
        )
        chunk_ids = (await session.scalars(select(Chunk.id).where(Chunk.note_id == note_id))).all()
        embeddings = 0
        if chunk_ids:
            embeddings = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Embedding)
                    .where(Embedding.chunk_id.in_(chunk_ids))
                )
                or 0
            )
        return chunks, embeddings


def test_delete_note_204_and_gone(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 删掉")
    )
    resp = env.client.delete(f"/api/notes/{note_id}")
    assert resp.status_code == 204, resp.text
    assert env.client.get(f"/api/notes/{note_id}").status_code == 404
    ids = [item["id"] for item in env.client.get("/api/notes?q=" + MARKER).json()["items"]]
    assert note_id not in ids


def test_delete_cascades_chunks_and_embeddings(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 级联")
    )
    env.client.portal.call(lambda: _seed_chunks_and_embedding(env.shared["factory"], note_id))
    assert (2, 1) == env.client.portal.call(lambda: _counts(env.shared["factory"], note_id)), (
        "种子本身要有 1 父 1 子共 2 块 + 1 向量(父块不 embed),否则这条测试什么都没证明"
    )
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 204
    assert (0, 0) == env.client.portal.call(lambda: _counts(env.shared["factory"], note_id)), (
        "chunks/embeddings 必须被 DB 级联清空,否则检索还会命中幽灵块"
    )


def test_delete_other_owner_404(env: SimpleNamespace) -> None:
    """owner 隔离:别人(owner 2)的笔记,对 owner 1 的请求就是 404。"""

    async def _seed_owner2() -> int:
        from app.repo import Repos

        async with env.shared["factory"]() as session:
            repos = Repos(session, 2)
            note = await repos.notes.create(
                source_id=None, title=MARKER + " 别人的", doc={"type": "doc", "content": []}
            )
            await session.commit()
            return note.id

    note_id = env.client.portal.call(_seed_owner2)
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 404


def test_delete_last_note_removes_source_and_blob(env: SimpleNamespace) -> None:
    blob = env.tmp_path / "blob-delete.txt"
    source_id = env.client.portal.call(lambda: _seed_source_with_blob(env.shared["factory"], blob))
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 连源删", source_id=source_id)
    )
    assert blob.exists()
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 204
    assert not blob.exists(), "孤儿 source 的原文件 blob 要从磁盘清掉"
    sources = env.client.get("/api/sources").json()["items"]
    assert source_id not in [s["id"] for s in sources]


def test_delete_shared_source_keeps_source(env: SimpleNamespace) -> None:
    """sources:notes = 1:N(ADR 0003):还有别的笔记挂着 source 就只删笔记,blob 不动。"""
    blob = env.tmp_path / "blob-shared.txt"
    source_id = env.client.portal.call(lambda: _seed_source_with_blob(env.shared["factory"], blob))
    first = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 共享1", source_id=source_id)
    )
    second = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 共享2", source_id=source_id)
    )
    assert env.client.delete(f"/api/notes/{first}").status_code == 204
    assert blob.exists(), "另一篇笔记还引用着,source 和 blob 都不能动"
    sources = env.client.get("/api/sources").json()["items"]
    assert source_id in [s["id"] for s in sources]
    assert env.client.delete(f"/api/notes/{second}").status_code == 204
    assert not blob.exists()


def test_delete_note_without_source(env: SimpleNamespace) -> None:
    """对话存笔记(source_id=None)也能删,不碰任何 source 逻辑。"""
    note_id = env.client.portal.call(
        lambda: _seed_note(env.shared["factory"], title=MARKER + " 无源")
    )
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 204
    assert env.client.get(f"/api/notes/{note_id}").status_code == 404


def test_delete_missing_404(env: SimpleNamespace) -> None:
    assert env.client.delete("/api/notes/99999999").status_code == 404
