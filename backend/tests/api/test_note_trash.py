"""纸篓(软删除)端点测试 —— api.md v37。

覆盖:
- 软删后:书架/详情/检索都不再出现,但 chunks/embeddings 保留(放回零成本);
- 纸篓清单带 archived_at / expires_at / days_left(retention_days=30);
- 放回:重新出现在书架,详情可读;
- 彻底删除:只认纸篓里的行(不许绕过软删直接毁 active 笔记);
- 30 天清理:purge_expired_trash 只删过期行,未过期的不动;
- owner 隔离:别人的纸篓看不见、动不了(纸篓是**账号级**的,不是浏览器级)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, update

from app.models import Chunk, Embedding, Note

MARKER = "trash-test"


async def _seed_note(factory: Any, *, title: str) -> int:
    from app.repo import Repos

    async with factory() as session:
        repos = Repos(session, 1)
        note = await repos.notes.create(
            source_id=None,
            title=title,
            doc={"type": "doc", "content": [{"type": "p", "text": MARKER + " 正文。"}]},
        )
        await session.commit()
        return note.id


async def _seed_chunks(factory: Any, note_id: int) -> None:
    from app.repo import Repos
    from app.service.chunk import Chunk as ChunkDC, ChunkResult

    async with factory() as session:
        repos = Repos(session, 1)
        result = ChunkResult(
            chunks=[
                ChunkDC(
                    text=MARKER + " 父块",
                    heading_path="P",
                    ord=0,
                    is_parent=True,
                    token_count=10,
                    section_idx=0,
                )
            ],
            sections=[],
            metrics={},
        )
        for _chunk, row in await repos.chunks.bulk_insert(note_id, result):
            session.add(
                Embedding(chunk_id=row.id, model="fake-model", dim=1024, vec=[0.5] * 1024)
            )
        await session.commit()


async def _chunk_count(factory: Any, note_id: int) -> int:
    async with factory() as session:
        rows = (await session.scalars(select(Chunk.id).where(Chunk.note_id == note_id))).all()
        return len(rows)


def test_soft_delete_hides_from_list_and_detail_but_keeps_chunks(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 软删"))
    env.client.portal.call(lambda: _seed_chunks(env.shared["factory"], note_id))

    assert env.client.delete(f"/api/notes/{note_id}").status_code == 204

    # 书架与详情都不再出现(所有查询本来就过滤 status='active')
    ids = [i["id"] for i in env.client.get("/api/notes?q=" + MARKER).json()["items"]]
    assert note_id not in ids, "软删后不许出现在书架列表"
    assert env.client.get(f"/api/notes/{note_id}").status_code == 404, "软删后详情按 404 口径"

    # 但子块仍在 —— 放回才能零成本
    assert env.client.portal.call(lambda: _chunk_count(env.shared["factory"], note_id)) == 1


def test_trash_list_reports_remaining_days(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 天数"))
    env.client.delete(f"/api/notes/{note_id}")

    body = env.client.get("/api/notes/trash").json()
    assert body["retention_days"] == 30
    item = next(i for i in body["items"] if i["id"] == note_id)
    assert item["days_left"] == 30, f"刚移入应当是整 30 天,实得 {item['days_left']}"
    assert item["expires_at"] > item["archived_at"]
    assert item["title"] == MARKER + " 天数"


def test_restore_puts_note_back(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 放回"))
    env.client.portal.call(lambda: _seed_chunks(env.shared["factory"], note_id))
    env.client.delete(f"/api/notes/{note_id}")

    resp = env.client.post(f"/api/notes/{note_id}/restore")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    # 回到书架、详情可读、子块仍在
    ids = [i["id"] for i in env.client.get("/api/notes?q=" + MARKER).json()["items"]]
    assert note_id in ids
    assert env.client.get(f"/api/notes/{note_id}").status_code == 200
    assert env.client.portal.call(lambda: _chunk_count(env.shared["factory"], note_id)) == 1

    # 纸篓里不再有它
    trash_ids = [i["id"] for i in env.client.get("/api/notes/trash").json()["items"]]
    assert note_id not in trash_ids


def test_purge_rejects_active_note(env: SimpleNamespace) -> None:
    """彻底删除只认纸篓里的行 —— 防止绕过软删直接毁一篇 active 笔记。"""
    note_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 不许"))
    assert env.client.delete(f"/api/notes/{note_id}/purge").status_code == 404
    assert env.client.get(f"/api/notes/{note_id}").status_code == 200, "笔记必须还在"


def test_restore_missing_is_404(env: SimpleNamespace) -> None:
    assert env.client.post("/api/notes/99999999/restore").status_code == 404


def test_purge_twice_second_is_404(env: SimpleNamespace) -> None:
    note_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 两次"))
    env.client.delete(f"/api/notes/{note_id}")
    assert env.client.delete(f"/api/notes/{note_id}/purge").status_code == 204
    assert env.client.delete(f"/api/notes/{note_id}/purge").status_code == 404


def test_soft_delete_twice_404(env: SimpleNamespace) -> None:
    """已在纸篓里的笔记再删一次 → 404(不泄漏状态,也不重复盖时间戳)。"""
    note_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 重复"))
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 204
    assert env.client.delete(f"/api/notes/{note_id}").status_code == 404


def test_trash_owner_isolation(env: SimpleNamespace) -> None:
    """纸篓是**账号级**的:owner 2 的纸篓在 owner 1 眼里是空的,也动不了。

    这是本次改动的核心动机之一 —— 旧的 localStorage 方案下,换账号/换浏览器就丢,
    且不同账号共用同一个 key(会互相清空)。
    """

    async def _seed_owner2_archived() -> tuple[int, int]:
        from app.repo import Repos

        async with env.shared["factory"]() as session:
            repos = Repos(session, 2)
            note = await repos.notes.create(
                source_id=None, title=MARKER + " 别人的纸篓", doc={"type": "doc", "content": []}
            )
            await session.commit()
            await repos.notes.archive_active(note.id)
            await session.commit()
            return note.id, note.owner_id

    note_id, _owner = env.client.portal.call(_seed_owner2_archived)

    trash_ids = [i["id"] for i in env.client.get("/api/notes/trash").json()["items"]]
    assert note_id not in trash_ids, "别人的纸篓不许出现在我的清单里"
    assert env.client.post(f"/api/notes/{note_id}/restore").status_code == 404
    assert env.client.delete(f"/api/notes/{note_id}/purge").status_code == 404


def test_cleanup_only_purges_expired(env: SimpleNamespace) -> None:
    """30 天清理:只删 archived_at 超期的行;刚移入的不许动。"""
    old_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 旧"))
    new_id = env.client.portal.call(lambda: _seed_note(env.shared["factory"], title=MARKER + " 新"))
    env.client.delete(f"/api/notes/{old_id}")
    env.client.delete(f"/api/notes/{new_id}")

    async def _backdate() -> None:
        async with env.shared["factory"]() as session:
            await session.execute(
                update(Note)
                .where(Note.id == old_id)
                .values(archived_at=datetime.now(tz=UTC) - timedelta(days=31))
            )
            await session.commit()

    env.client.portal.call(_backdate)

    async def _run_cleanup() -> list[str]:
        # 用 fixture 的事务内 session 调核心实现 —— arq 任务自己开 session(真提交),
        # 那会绕过 conftest 的外层事务回滚,测试数据就泄漏进真库了
        from app.worker.worker import purge_expired_trash_in

        async with env.shared["factory"]() as session:
            blobs = await purge_expired_trash_in(session)
            await session.commit()
            return blobs

    env.client.portal.call(_run_cleanup)

    trash_ids = [i["id"] for i in env.client.get("/api/notes/trash").json()["items"]]
    assert old_id not in trash_ids, "满 30 天的必须已被彻底删除"
    assert new_id in trash_ids, "刚移入的不许被清掉"
