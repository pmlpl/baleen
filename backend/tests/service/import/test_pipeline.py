"""导入状态机测试(合成 fixture,不读 eval/samples)。

覆盖 HANDOFF 线 I-批1 §5 的四条:
1) 同文件导入两次 → sha256 去重,不重复处理(且不重复调 G2);
2) G1 reject → 不产 note,jobs 状态正确;
3) 正常 note → 父块/子块/embeddings 数量关系正确(父块无 embedding 行);
4) 中途失败 → jobs 记 failed + last_error,可重试。
另加 dry-run 不落库。

⚠️ 开发库里可能已有历史数据,所以断言一律按本次的 sha256 / source_id 过滤,不数全表。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Chunk, Embedding, Job, Note, ReviewRun, Source
from app.service.imports import run_import_async
from app.service.llm import ChatResult

_PARAGRAPH = (
    "闭包是函数与其词法环境的组合,函数在定义时就捕获了外层作用域中的变量。"
    "即使外层函数已经返回,闭包仍然可以访问那些变量,这也是很多内存泄漏问题的根源。"
    "排查时可以在浏览器开发者工具里做堆快照,对比两次快照之间新增的 retained objects。"
    "修复办法是显式把不再需要的引用置空,或者把事件监听的注册与注销成对写。"
)
NOTE_MD = (
    "# 闭包笔记\n\n"
    + "\n\n".join([_PARAGRAPH] * 3)
    + "\n\n## 正确写法\n\n"
    + "\n\n".join([_PARAGRAPH] * 3)
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(path: Path) -> str:
    return f"import:{_sha(path)}"


async def _count(session, model, *criteria) -> int:
    stmt = select(func.count()).select_from(model)
    for criterion in criteria:
        stmt = stmt.where(criterion)
    return int(await session.scalar(stmt) or 0)


class _FakeLLM:
    """最小 fake:只实现 judge_g2 需要的 chat()/model,不联网。"""

    model = "fake-llm"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, **kwargs) -> ChatResult:
        self.calls += 1
        payload = {
            "verdict": "accept",
            "quality": "core",
            "note_type": "case",
            "score": 4,
            "action": "index",
            "reasons": ["fake"],
            "missing": [],
        }
        return ChatResult(
            content=json.dumps(payload, ensure_ascii=False),
            model=self.model,
            total_tokens=10,
        )


def _kwargs(factory, embed, tokens) -> dict:
    return {
        "no_llm": True,
        "session_factory": factory,
        "embed_func": embed,
        "token_counter": tokens,
    }


class TestDedupe:
    def test_same_file_twice_hits_sha256(self, tmp_path, db_scope, fake_embed, fake_tokens):
        path = _write(tmp_path, "note.md", NOTE_MD)

        async def case():
            async with db_scope() as factory:
                kwargs = _kwargs(factory, fake_embed, fake_tokens)
                first = await run_import_async(path, **kwargs)
                second = await run_import_async(path, **kwargs)
                assert first.deduped is False
                assert second.deduped is True
                assert first.source_id == second.source_id
                assert first.note_id == second.note_id
                assert first.note_id is not None
                async with factory() as session:
                    assert await _count(session, Source, Source.sha256 == _sha(path)) == 1
                    assert await _count(session, Note, Note.source_id == first.source_id) == 1
                    assert await _count(session, Job, Job.dedupe_key == _key(path)) == 1

        asyncio.run(case())

    def test_dedupe_does_not_call_g2_again(self, tmp_path, db_scope, fake_embed, fake_tokens):
        path = _write(tmp_path, "note.md", NOTE_MD)
        fake = _FakeLLM()

        async def case():
            async with db_scope() as factory:
                kwargs = {
                    **_kwargs(factory, fake_embed, fake_tokens),
                    "no_llm": False,
                    "client": fake,
                    "charter": "只收自己想明白的东西",
                }
                first = await run_import_async(path, **kwargs)
                calls_after_first = fake.calls
                second = await run_import_async(path, **kwargs)
                assert first.g2_verdict == "accept"
                assert second.deduped is True
                # 首轮可能有 G2 + 标题整理两次调用;关键是第二轮一次都不调
                assert fake.calls == calls_after_first, "去重命中不许重复调用 LLM(省钱)"
                async with factory() as session:
                    g2_rows = list(
                        await session.scalars(
                            select(ReviewRun).where(
                                ReviewRun.gate == "G2",
                                ReviewRun.source_id == first.source_id,
                            )
                        )
                    )
                    assert len(g2_rows) == 1

        asyncio.run(case())


class TestG1Reject:
    def test_reject_produces_no_note(self, tmp_path, db_scope, fake_embed, fake_tokens):
        path = _write(tmp_path, "hello.txt", "hello, world!")

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(path, **_kwargs(factory, fake_embed, fake_tokens))
                assert result.parse_status == "ok"
                assert result.g1_verdict == "reject"
                assert result.note_id is None
                assert result.chunks_total == 0
                async with factory() as session:
                    assert await _count(session, Note, Note.source_id == result.source_id) == 0
                    job = await session.scalar(select(Job).where(Job.dedupe_key == _key(path)))
                    assert job is not None and job.status == "done"
                    g1 = await session.scalar(
                        select(ReviewRun).where(
                            ReviewRun.gate == "G1",
                            ReviewRun.source_id == result.source_id,
                        )
                    )
                    assert g1 is not None and g1.verdict == "reject"

        asyncio.run(case())


class TestChunksAndEmbeddings:
    def test_parent_chunks_have_no_embedding(self, tmp_path, db_scope, fake_embed, fake_tokens):
        path = _write(tmp_path, "note.md", NOTE_MD)

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(path, **_kwargs(factory, fake_embed, fake_tokens))
                assert result.note_id is not None
                async with factory() as session:
                    rows = list(
                        await session.scalars(select(Chunk).where(Chunk.note_id == result.note_id))
                    )
                    parents = [r for r in rows if r.is_parent]
                    children = [r for r in rows if not r.is_parent]
                    assert parents, "至少应有一个父块"
                    assert children, "至少应有一个子块"
                    assert all(r.parent_id in {p.id for p in parents} for r in children)

                    emb_rows = list(
                        await session.scalars(
                            select(Embedding).where(Embedding.chunk_id.in_([r.id for r in rows]))
                        )
                    )
                    emb_ids = {e.chunk_id for e in emb_rows}
                    parent_ids = {p.id for p in parents}
                    assert emb_ids.isdisjoint(parent_ids), "父块不许有 embedding 行"
                    # 本 fixture 没有目录页/图片,所有子块都应被向量化
                    assert emb_ids == {r.id for r in children}
                    assert result.embeddings == len(children)
                    assert result.chunks_parents == len(parents)
                    assert result.chunks_children == len(children)

        asyncio.run(case())


class TestFailureAndRetry:
    def test_embed_failure_marks_job_failed_then_retry(
        self, tmp_path, db_scope, fake_embed, fake_tokens
    ):
        path = _write(tmp_path, "note.md", NOTE_MD)
        calls = {"n": 0}

        def flaky_embed(texts):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("embed boom")
            return fake_embed(texts)

        async def case():
            async with db_scope() as factory:
                kwargs = {
                    "no_llm": True,
                    "session_factory": factory,
                    "token_counter": fake_tokens,
                }
                with pytest.raises(RuntimeError, match="embed boom"):
                    await run_import_async(path, embed_func=flaky_embed, **kwargs)

                async with factory() as session:
                    job = await session.scalar(select(Job).where(Job.dedupe_key == _key(path)))
                    assert job is not None
                    assert job.status == "failed"
                    assert "embed boom" in (job.last_error or "")
                    # note 与 chunks 在失败前已提交,应可续跑复用
                    source = await session.scalar(select(Source).where(Source.sha256 == _sha(path)))
                    assert source is not None
                    note = await session.scalar(select(Note).where(Note.source_id == source.id))
                    assert note is not None
                    assert await _count(session, Chunk, Chunk.note_id == note.id) > 0
                    assert (
                        await _count(
                            session,
                            Embedding,
                            Embedding.chunk_id.in_(
                                select(Chunk.id).where(Chunk.note_id == note.id)
                            ),
                        )
                        == 0
                    )

                retry = await run_import_async(path, embed_func=fake_embed, **kwargs)
                assert retry.note_id is not None
                assert retry.embeddings > 0
                async with factory() as session:
                    job = await session.scalar(select(Job).where(Job.dedupe_key == _key(path)))
                    assert job is not None and job.status == "done"
                    assert job.last_error is None
                    assert await _count(session, Note, Note.source_id == retry.source_id) == 1

        asyncio.run(case())


class TestDryRun:
    def test_dry_run_does_not_touch_db(self, tmp_path, db_scope):
        path = _write(tmp_path, "note.md", NOTE_MD)

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(path, dry_run=True)
                assert result.dry_run is True
                assert result.source_id is None and result.note_id is None
                assert result.g1_verdict in {"accept", "reject"}
                async with factory() as session:
                    assert await _count(session, Source, Source.sha256 == _sha(path)) == 0
                    assert await _count(session, Job, Job.dedupe_key == _key(path)) == 0

        asyncio.run(case())


class _RoutingLLM:
    """按 system prompt 区分 G2 与标题整理,各自返回可预期的内容。"""

    model = "fake-routing-llm"

    def __init__(self, outline_json: str = "[]") -> None:
        self.calls: list[str] = []
        self._outline_json = outline_json

    def chat(self, messages, **kwargs) -> ChatResult:
        system = messages[0]["content"] if messages else ""
        if "文档结构整理助手" in system:
            self.calls.append("rewrite")
            content = self._outline_json
        else:
            self.calls.append("g2")
            content = json.dumps(
                {
                    "verdict": "accept",
                    "quality": "core",
                    "note_type": "case",
                    "score": 4,
                    "action": "index",
                    "reasons": ["fake"],
                    "missing": [],
                },
                ensure_ascii=False,
            )
        return ChatResult(content=content, model=self.model, total_tokens=10)


class TestHeadingRewrite:
    """导入时的标题结构二次加工(仅在 no_llm=False 且结构不佳时触发)。"""

    def test_long_doc_without_headings_gets_rewritten(
        self, tmp_path, db_scope, fake_embed, fake_tokens
    ):
        # 一段很长的纯正文(无标题),把行号与标题计划对上
        body = "\n\n".join([_PARAGRAPH] * 4)
        path = _write(tmp_path, "plain.md", body)
        llm = _RoutingLLM('[{"at": 0, "level": 1, "title": "闭包与内存泄漏"}]')

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(
                    path,
                    no_llm=False,
                    session_factory=factory,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    client=llm,
                    charter="只收自己想明白的东西",
                )
                assert result.headings_rewritten is True
                assert result.headings_added == 1
                assert "rewrite" in llm.calls
                async with factory() as session:
                    note = await session.scalar(
                        select(Note).where(Note.source_id == result.source_id)
                    )
                    assert note is not None
                    # 笔记标题仍是原文派生(没被重写的 H1 顶掉)
                    assert note.title == "plain"
                    doc_headings = [
                        b
                        for b in (note.doc or {}).get("content", [])
                        if b.get("type") == "heading"
                    ]
                    assert doc_headings, "应当插入了标题块"
                    text = "".join(n.get("text", "") for n in doc_headings[0]["content"])
                    assert text.startswith("一、")
                    assert "闭包与内存泄漏" in text

        asyncio.run(case())

    def test_no_llm_skips_rewrite(self, tmp_path, db_scope, fake_embed, fake_tokens):
        body = "\n\n".join([_PARAGRAPH] * 4)
        path = _write(tmp_path, "skip.md", body)
        llm = _RoutingLLM('[{"at": 0, "level": 1, "title": "不该出现"}]')

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(
                    path,
                    no_llm=True,
                    session_factory=factory,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    client=llm,
                )
                assert result.headings_rewritten is False
                assert "rewrite" not in llm.calls  # 一次 LLM 都没调

        asyncio.run(case())

    def test_well_structured_doc_is_not_rewritten(
        self, tmp_path, db_scope, fake_embed, fake_tokens
    ):
        # 已经带编号且无重名 → needs_rewrite 返回 False,不该花 LLM
        md = (
            "# 一、总览\n\n" + _PARAGRAPH + "\n\n"
            "# 二、细节\n\n" + _PARAGRAPH + "\n\n"
            "## 2.1 子节甲\n\n" + _PARAGRAPH + "\n\n"
            "## 2.2 子节乙\n\n" + _PARAGRAPH
        )
        path = _write(tmp_path, "good.md", md)
        llm = _RoutingLLM('[{"at": 0, "level": 1, "title": "不该出现"}]')

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(
                    path,
                    no_llm=False,
                    session_factory=factory,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    client=llm,
                    charter="只收自己想明白的东西",
                )
                assert result.headings_rewritten is False
                assert "rewrite" not in llm.calls
                assert llm.calls.count("g2") == 1  # G2 照常跑

        asyncio.run(case())
