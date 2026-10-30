"""存量笔记标题回填测试:真 PG(外层事务回滚)+ FakeTransport LLM + 假 embed。

覆盖:整理并重建 / 结构已好跳过 / 已有标记跳过 / 空计划不打标记 /
LLM 失败单篇回滚 / list_candidates 判定 / delete_by_note 级联清 embeddings。

⚠️ 每个用例只开一个 db_scope:播种/执行/验证共用同一个 factory,
   外层事务未提交的数据只在本连接可见。
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from sqlalchemy import select

from app.models import Chunk, Embedding, Note, Source
from app.repo import Repos
from app.service.chunk import chunk
from app.service.embed import EMBEDDING_MODEL
from app.service.llm import LLMClient
from app.service.parse.blocks import heading, paragraph_from_text
from app.service.rewrite.backfill import (
    NO_SPECS,
    REWRITTEN,
    SKIPPED_DONE,
    SKIPPED_STRUCTURE,
    backfill_notes,
    list_candidates,
)

OLD_PARENT_TEXT = "旧父块文本"
OLD_CHILD_TEXT = "旧子块文本"


class FakeTransport:
    def __init__(self, responses: list[tuple]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        status, raw = self._responses.pop(0)
        return status, {}, raw

    def request_stream(self, *a, **kw):
        raise NotImplementedError


class _BoomClient:
    """chat 直接炸的假 client,验证单篇失败回滚不中断整批。"""

    model = "boom"

    def chat(self, messages, **kwargs):
        raise RuntimeError("网关挂了")


def _chat(content: str) -> bytes:
    return json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
    ).encode()


def _client(content: str) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport([(200, _chat(content))])
    client = LLMClient(
        base_url="https://api.example.test/v1",
        api_key="sk-test",
        model="fake-model",
        transport=transport,
        sleep=lambda _s: None,
    )
    return client, transport


def _doc_messy() -> dict:
    """存量笔记的典型病灶:同级重名 + 全程无编号 → needs_rewrite 必 True。"""
    return {
        "type": "doc",
        "content": [
            heading(1, "错误处理"),
            paragraph_from_text("字" * 300),
            heading(2, "用 fetch 的场合"),
            paragraph_from_text("字" * 300),
            heading(2, "用 fetch 的场合"),
            paragraph_from_text("字" * 300),
        ],
    }


def _doc_good() -> dict:
    """结构已经不错:多标题、无重名、都带编号 → 跳过。"""
    return {
        "type": "doc",
        "content": [
            heading(1, "一、错误处理"),
            heading(2, "1.1 fetch 写法"),
            paragraph_from_text("字" * 300),
            heading(1, "二、并发请求"),
            heading(2, "2.1 并发控制"),
            paragraph_from_text("字" * 300),
        ],
    }


async def _seed_note(
    factory, *, doc: dict, title: str = "旧标题", done: bool = False, with_chunks: bool = True
) -> tuple[int, int]:
    """造一篇存量笔记(可选:旧 chunks + 旧 embedding / 已整理标记)。"""
    async with factory() as session:
        repos = Repos(session, owner_id=1)
        source, _ = await repos.sources.create_or_get_by_sha256(
            sha256=uuid4().hex,
            filename="old.md",
            ext=".md",
            size_bytes=10,
            storage_path="storage/old.md",
            parse_status="ok",
        )
        if done:
            source.parse_meta = {"headings_rewritten": True}
        note = await repos.notes.create(source_id=source.id, title=title, doc=doc)
        if with_chunks:
            parent = Chunk(
                note_id=note.id,
                parent_id=None,
                ord=0,
                heading_path="旧标题",
                text=OLD_PARENT_TEXT,
                token_count=5,
                is_parent=True,
                meta={"block_type": "section"},
            )
            session.add(parent)
            await session.flush()
            child = Chunk(
                note_id=note.id,
                parent_id=parent.id,
                ord=1,
                heading_path=None,
                text=OLD_CHILD_TEXT,
                token_count=5,
                is_parent=False,
                meta={},
            )
            session.add(child)
            await session.flush()
            session.add(
                Embedding(
                    chunk_id=child.id,
                    model=EMBEDDING_MODEL,
                    dim=1024,
                    vec=[0.1] * 1024,
                )
            )
        await session.commit()
        return source.id, note.id


async def _get_note(session, note_id: int) -> Note:
    return await session.scalar(select(Note).where(Note.id == note_id))


async def _get_source(session, source_id: int) -> Source:
    return await session.scalar(select(Source).where(Source.id == source_id))


async def _note_embeddings(session, note_id: int) -> list[Embedding]:
    stmt = (
        select(Embedding)
        .join(Chunk, Embedding.chunk_id == Chunk.id)
        .where(Chunk.note_id == note_id)
    )
    return list((await session.scalars(stmt)).all())


# ---------------- 正向:整理 + 重建 ----------------


def test_backfill_rewrites_and_rebuilds(db_scope, fake_embed, fake_tokens) -> None:
    """LLM 在既有标题位置插编号版时,chunker 会把空 section 丢掉(保留原标题),
    这是 chunker 既有语义(pipeline 同款),这里只断言 doc 编号化 + 全量重建。"""

    async def case() -> None:
        async with db_scope() as factory:
            source_id, note_id = await _seed_note(factory, doc=_doc_messy())
            client, transport = _client(
                '[{"at": 0, "level": 1, "title": "错误处理"},'
                ' {"at": 2, "level": 2, "title": "fetch 场合"},'
                ' {"at": 4, "level": 2, "title": "axios 场合"}]'
            )
            async with factory() as session:
                outcomes = await backfill_notes(
                    session,
                    1,
                    client=client,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    note_ids=[note_id],
                )

            assert len(outcomes) == 1
            out = outcomes[0]
            assert out.status == REWRITTEN
            assert out.headings_added == 3
            assert out.old_chunks == 2  # 播种的旧父块+旧子块
            assert out.new_chunks > 0
            assert out.embedded > 0
            assert len(transport.calls) == 1

            async with factory() as session:
                note = await _get_note(session, note_id)
                source = await _get_source(session, source_id)
                # 标题带编号(编号是代码算的),title 不被覆盖
                texts = [
                    b["content"][0]["text"]
                    for b in note.doc["content"]
                    if b["type"] == "heading"
                ]
                assert "一、错误处理" in texts
                assert "1.1 fetch 场合" in texts
                assert note.title == "旧标题"
                assert source.parse_meta["headings_rewritten"] is True

                chunks = list(
                    (await session.scalars(select(Chunk).where(Chunk.note_id == note_id))).all()
                )
                assert all(c.text not in (OLD_PARENT_TEXT, OLD_CHILD_TEXT) for c in chunks)

                embeddings = await _note_embeddings(session, note_id)
                assert len(embeddings) == out.embedded
                assert all(e.vec and float(e.vec[0]) == 0.5 for e in embeddings)  # 假向量

    asyncio.run(case())


def test_backfill_numbered_paths_for_new_sections(db_scope, fake_embed, fake_tokens) -> None:
    """无标题长文:LLM 在内容块前补标题 → 编号进 heading_path(检索路径的前缀价值)。"""

    async def case() -> None:
        doc = {
            "type": "doc",
            "content": [paragraph_from_text("段" * 300) for _ in range(6)],
        }
        async with db_scope() as factory:
            _source_id, note_id = await _seed_note(factory, doc=doc)
            client, _transport = _client(
                '[{"at": 0, "level": 1, "title": "开篇"},'
                ' {"at": 2, "level": 2, "title": "fetch 场合"},'
                ' {"at": 4, "level": 2, "title": "axios 场合"}]'
            )
            async with factory() as session:
                outcomes = await backfill_notes(
                    session,
                    1,
                    client=client,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    note_ids=[note_id],
                )
            assert outcomes[0].status == REWRITTEN

            async with factory() as session:
                chunks = list(
                    (await session.scalars(select(Chunk).where(Chunk.note_id == note_id))).all()
                )
                paths = " | ".join(c.heading_path or "" for c in chunks)
                assert "一、开篇" in paths
                assert "1.1 fetch 场合" in paths
                assert "1.2 axios 场合" in paths

    asyncio.run(case())


# ---------------- 跳过路径 ----------------


def test_backfill_skips_well_structured(db_scope, fake_embed, fake_tokens) -> None:
    async def case() -> None:
        async with db_scope() as factory:
            _source_id, note_id = await _seed_note(factory, doc=_doc_good())
            client, transport = _client("[]")
            async with factory() as session:
                outcomes = await backfill_notes(
                    session,
                    1,
                    client=client,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    note_ids=[note_id],
                )
            assert outcomes[0].status == SKIPPED_STRUCTURE
            assert transport.calls == []  # 一次 LLM 都没调

            async with factory() as session:
                note = await _get_note(session, note_id)
                assert note.doc["content"][0]["type"] == "heading"  # doc 没动
                embeddings = await _note_embeddings(session, note_id)
                assert len(embeddings) == 1  # 旧向量还在

    asyncio.run(case())


def test_backfill_skips_already_done(db_scope, fake_embed, fake_tokens) -> None:
    async def case() -> None:
        async with db_scope() as factory:
            _source_id, note_id = await _seed_note(factory, doc=_doc_messy(), done=True)
            client, transport = _client("[]")
            async with factory() as session:
                outcomes = await backfill_notes(
                    session,
                    1,
                    client=client,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    note_ids=[note_id],
                )
            assert outcomes[0].status == SKIPPED_DONE
            assert transport.calls == []

    asyncio.run(case())


# ---------------- 空计划与失败 ----------------


def test_backfill_no_specs_leaves_db_untouched(db_scope, fake_embed, fake_tokens) -> None:
    """LLM 返回空计划(坏模型/空 content)→ 不打标记、不改库,可重试。"""

    async def case() -> None:
        async with db_scope() as factory:
            source_id, note_id = await _seed_note(factory, doc=_doc_messy())
            client, _transport = _client("抱歉,我无法处理")
            async with factory() as session:
                outcomes = await backfill_notes(
                    session,
                    1,
                    client=client,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    note_ids=[note_id],
                )
            assert outcomes[0].status == NO_SPECS

            async with factory() as session:
                note = await _get_note(session, note_id)
                source = await _get_source(session, source_id)
                assert note.doc["content"][0]["content"][0]["text"] == "错误处理"  # 原样
                assert "headings_rewritten" not in (source.parse_meta or {})
                embeddings = await _note_embeddings(session, note_id)
                assert len(embeddings) == 1  # 旧向量还在

    asyncio.run(case())


def test_backfill_llm_failure_rolls_back_note(db_scope, fake_embed, fake_tokens) -> None:
    async def case() -> None:
        async with db_scope() as factory:
            source_id, note_id = await _seed_note(factory, doc=_doc_messy())
            async with factory() as session:
                outcomes = await backfill_notes(
                    session,
                    1,
                    client=_BoomClient(),  # type: ignore[arg-type]
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    note_ids=[note_id],
                )
            out = outcomes[0]
            assert out.status == "failed"
            assert "RuntimeError" in (out.error or "")

            async with factory() as session:
                note = await _get_note(session, note_id)
                source = await _get_source(session, source_id)
                assert note.doc["content"][0]["content"][0]["text"] == "错误处理"  # 回滚后原样
                assert "headings_rewritten" not in (source.parse_meta or {})
                chunks = list(
                    (await session.scalars(select(Chunk).where(Chunk.note_id == note_id))).all()
                )
                assert {c.text for c in chunks} == {OLD_PARENT_TEXT, OLD_CHILD_TEXT}

    asyncio.run(case())


# ---------------- list_candidates / delete_by_note ----------------


def test_list_candidates_reports_verdicts(db_scope) -> None:
    async def case() -> None:
        async with db_scope() as factory:
            _s1, n1 = await _seed_note(factory, doc=_doc_messy(), title="要整理的")
            _s2, n2 = await _seed_note(factory, doc=_doc_good(), title="已很好的", done=True)
            async with factory() as session:
                rows = await list_candidates(session, 1)
                other_owner = await list_candidates(session, 2)
        by_id = {r["note_id"]: r for r in rows}
        assert by_id[n1]["needs_rewrite"] is True
        assert by_id[n1]["already_done"] is False
        assert by_id[n1]["headings"] == 3
        assert by_id[n2]["already_done"] is True
        assert other_owner == []  # owner 隔离

    asyncio.run(case())


def test_delete_by_note_cascades_embeddings(db_scope) -> None:
    async def case() -> None:
        async with db_scope() as factory:
            _source_id, note_id = await _seed_note(factory, doc=_doc_good(), with_chunks=False)
            async with factory() as session:
                repos = Repos(session, owner_id=1)
                result = chunk(_doc_good(), token_counter=lambda t: max(1, len(t)))
                inserted = await repos.chunks.bulk_insert(note_id, result)
                child_ids = [row.id for _c, row in inserted]
                await repos.embeddings.bulk_insert(
                    [(cid, EMBEDDING_MODEL, 1024, [0.2] * 1024) for cid in child_ids]
                )
                await session.commit()

                assert await repos.chunks.count_by_note(note_id) == len(result.chunks)
                deleted = await repos.chunks.delete_by_note(note_id)
                assert deleted == len(result.chunks)
                assert await repos.chunks.count_by_note(note_id) == 0
                embeddings = await _note_embeddings(session, note_id)
                assert embeddings == []  # FK CASCADE 顺带清干净

    asyncio.run(case())
