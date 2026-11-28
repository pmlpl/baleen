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
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import Chunk, Embedding, Job, Note, ReviewRun, Source
from app.repo.repos import Repos
from app.service.imports import run_import_async
from app.service.imports.pipeline import DEDUPE_PREFIX
from app.service.llm import ChatResult, LLMError

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
    """假 LLM：organizer 调用时返回预设的 JSON（organizable + markdown）。"""

    model = "fake-routing-llm"

    def __init__(self, organizer_json: str | None = None) -> None:
        self.calls: list[str] = []
        # 默认返回一个可整理的成功响应
        self._organizer_json = organizer_json or json.dumps(
            {
                "organizable": True,
                "markdown": "# 整理后的标题\n\n## 小节\n\n" + _PARAGRAPH,
                "quality": "core",
                "note_type": "concept",
                "reasons": ["整理了内容"],
            },
            ensure_ascii=False,
        )

    def chat(self, messages, **kwargs) -> ChatResult:
        system = messages[0]["content"] if messages else ""
        if "笔记编辑助手" in system:
            self.calls.append("organize")
            content = self._organizer_json
        else:
            self.calls.append("other")
            content = "{}"
        return ChatResult(content=content, model=self.model, total_tokens=10)


class TestHeadingRewrite:
    """AI 笔记整理（替代 G2 + 标题整理）：no_llm=False 时 always 跑 organizer。"""

    def test_long_doc_without_headings_gets_rewritten(
        self, tmp_path, db_scope, fake_embed, fake_tokens
    ):
        # 一段很长的纯正文(无标题),organizer 应返回带标题的 markdown
        body = "\n\n".join([_PARAGRAPH] * 4)
        path = _write(tmp_path, "plain.md", body)
        llm = _RoutingLLM(
            json.dumps(
                {
                    "organizable": True,
                    "markdown": "# 闭包与内存泄漏\n\n## 定义\n\n" + _PARAGRAPH,
                    "quality": "core",
                    "note_type": "concept",
                    "reasons": ["补了标题"],
                },
                ensure_ascii=False,
            )
        )

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(
                    path,
                    no_llm=False,
                    session_factory=factory,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    client=llm,
                )
                assert result.headings_rewritten is True
                assert "organize" in llm.calls
                async with factory() as session:
                    note = await session.scalar(
                        select(Note).where(Note.source_id == result.source_id)
                    )
                    assert note is not None
                    # 笔记标题用 organizer 重写后的首 H1(去编号),不再卡在原文派生值
                    # —— 否则视频导入等"原始 H1 = 平台文案"的场景会得到一长串 blob 标题
                    assert note.title == "闭包与内存泄漏"
                    doc_headings = [
                        b for b in (note.doc or {}).get("content", []) if b.get("type") == "heading"
                    ]
                    assert doc_headings, "应当有标题块"
                    text = "".join(n.get("text", "") for n in doc_headings[0]["content"])
                    assert text.startswith("一、")
                    assert "闭包与内存泄漏" in text

        asyncio.run(case())

    def test_no_llm_skips_organize(self, tmp_path, db_scope, fake_embed, fake_tokens):
        body = "\n\n".join([_PARAGRAPH] * 4)
        path = _write(tmp_path, "skip.md", body)
        llm = _RoutingLLM()

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
                assert len(llm.calls) == 0  # 一次 LLM 都没调

        asyncio.run(case())

    def test_well_structured_doc_still_organized(self, tmp_path, db_scope, fake_embed, fake_tokens):
        # organizer always runs (no needs_rewrite check) — 验证它跑了且产出有标题
        md = (
            "# 一、总览\n\n" + _PARAGRAPH + "\n\n"
            "# 二、细节\n\n" + _PARAGRAPH + "\n\n"
            "## 2.1 子节甲\n\n" + _PARAGRAPH + "\n\n"
            "## 2.2 子节乙\n\n" + _PARAGRAPH
        )
        path = _write(tmp_path, "good.md", md)
        llm = _RoutingLLM(
            json.dumps(
                {
                    "organizable": True,
                    "markdown": "# 总览\n\n" + _PARAGRAPH + "\n\n## 细节\n\n" + _PARAGRAPH,
                    "quality": "normal",
                    "note_type": "concept",
                    "reasons": ["结构良好，轻度整理"],
                },
                ensure_ascii=False,
            )
        )

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(
                    path,
                    no_llm=False,
                    session_factory=factory,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    client=llm,
                )
                assert result.headings_rewritten is True  # organizer 跑了
                assert "organize" in llm.calls

        asyncio.run(case())


class TestEvalCapture:
    """「使用即积累」:真实导入(非 dedupe)完成 → 原文件归档进 eval/candidates/。"""

    def test_success_import_archives_candidate(
        self, tmp_path, db_scope, fake_embed, fake_tokens, monkeypatch
    ):
        from app.service import eval_capture as cap

        cdir, logp = tmp_path / "cands", tmp_path / "log.csv"
        monkeypatch.setattr(cap, "CANDIDATES_DIR", cdir)
        monkeypatch.setattr(cap, "LOG_PATH", logp)
        monkeypatch.setattr(cap, "get_settings", lambda: SimpleNamespace(eval_capture_enabled=True))
        path = _write(tmp_path, "note.md", NOTE_MD)

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(path, **_kwargs(factory, fake_embed, fake_tokens))
                assert result.note_id is not None and result.deduped is False

        asyncio.run(case())

        files = list(cdir.iterdir())
        assert len(files) == 1 and files[0].read_bytes() == path.read_bytes()
        rows = list(csv.DictReader(logp.open(encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["parse_status"] == "ok" and rows[0]["note_id"] != ""

    def test_dedupe_hit_does_not_log_again(
        self, tmp_path, db_scope, fake_embed, fake_tokens, monkeypatch
    ):
        from app.service import eval_capture as cap

        cdir, logp = tmp_path / "cands", tmp_path / "log.csv"
        monkeypatch.setattr(cap, "CANDIDATES_DIR", cdir)
        monkeypatch.setattr(cap, "LOG_PATH", logp)
        monkeypatch.setattr(cap, "get_settings", lambda: SimpleNamespace(eval_capture_enabled=True))
        path = _write(tmp_path, "note.md", NOTE_MD)

        async def case():
            async with db_scope() as factory:
                kwargs = _kwargs(factory, fake_embed, fake_tokens)
                await run_import_async(path, **kwargs)
                await run_import_async(path, **kwargs)  # dedupe

        asyncio.run(case())

        assert len(list(cdir.iterdir())) == 1, "文件只归档一份"
        rows = list(csv.DictReader(logp.open(encoding="utf-8")))
        assert len(rows) == 1, "dedupe 命中不再登记(第一次导入已记过)"


def test_video_keep_meta_prefix_convention() -> None:
    """G0 落盘保留键(2026-11-26 C4,白名单退役):video_ 前缀自动保留 + 例外键集合。

    旧 7 键硬编码白名单的问题:worker 阶段写新键要手工加白,忘了就被 G0 静默丢
    (治本批踩过)。前缀约定从机制上根除——新键带 video_ 前缀即自动保留。
    """
    from app.service.imports.pipeline import _VIDEO_KEEP_EXTRA, video_keep_meta

    prev = {
        "video_url": "u",
        "video_mode": "whisper",
        "video_log": [],
        "video_new_field": 1,  # 新键只需带前缀,无需任何手工登记
        "content_sha256": "sha",  # 例外键:无前缀但必须保留(去重键)
        "llm_transcript_fix": {"n": 1},  # 例外键:转写留痕
        "warnings": ["别的"],  # 无前缀非例外 → G0 重建时由 pr 侧接管,不保留
    }
    kept = video_keep_meta(prev)
    assert set(kept) == {
        "video_url",
        "video_mode",
        "video_log",
        "video_new_field",
        "content_sha256",
        "llm_transcript_fix",
    }
    # 例外键集合钉死:新例外必须显式进 _VIDEO_KEEP_EXTRA(写进 pipeline.py 注释)
    assert _VIDEO_KEEP_EXTRA == {"content_sha256", "llm_transcript_fix"}


class TestOrganizeFailurePaths:
    """整理失败的两种去向（2026-11-28 主人拍板）：

    - 视频导入：整理失败=导入失败、不入库——裸转写入库只会污染检索+假装完成
      （11876 首例：LLM 网络窗口三层降级，一墙错字原文进了库还召不回）；
    - 普通文件：保持原文入库——文件本身可读，不因 LLM 抽风丢用户上传。
    """

    class _BoomLLM(_RoutingLLM):
        """organize 调用必炸的假 LLM（模拟 LLM 网关不可达）。"""

        def chat(self, messages, **kwargs) -> ChatResult:
            system = messages[0]["content"] if messages else ""
            if "笔记编辑助手" in system:
                self.calls.append("organize")
                raise LLMError("网络错误:[WinError 10061] 由于目标计算机积极拒绝，无法连接。")
            self.calls.append("other")
            return ChatResult(content="{}", model=self.model, total_tokens=10)

    def test_video_organize_failure_fails_import_without_note(
        self, tmp_path, db_scope, fake_embed, fake_tokens
    ):
        md = "# 视频转写\n\n" + "\n\n".join([_PARAGRAPH] * 3)
        path = _write(tmp_path, "video.md", md)

        async def case():
            async with db_scope() as factory:
                # 预创建视频 source + job（复刻 api/upload.py whisper 异步路径的最小形态）
                async with factory() as session:
                    repos = Repos(session, owner_id=1)
                    source, _ = await repos.sources.create_or_get_by_sha256(
                        sha256="video:BVFAIL:whisper",
                        filename="BVFAIL.md",
                        ext=".md",
                        size_bytes=0,
                        storage_path="",
                        mime="text/markdown",
                        parse_status="pending",
                    )
                    await repos.sources.update_parse_meta(
                        source.id,
                        {
                            "video_url": "https://www.bilibili.com/video/BVFAIL",
                            "video_mode": "whisper",
                        },
                    )
                    job, _ = await repos.jobs.create_or_get(
                        dedupe_key=DEDUPE_PREFIX + "video:BVFAIL:whisper",
                        payload={"url": "u", "mode": "whisper", "source_id": source.id},
                        kind="import",
                    )
                    await session.commit()
                    source_id, job_id = source.id, job.id

                llm = self._BoomLLM()
                with pytest.raises(RuntimeError, match="笔记未入库"):
                    await run_import_async(
                        path,
                        no_llm=False,
                        session_factory=factory,
                        embed_func=fake_embed,
                        token_counter=fake_tokens,
                        client=llm,
                        source_id=source_id,
                    )
                assert "organize" in llm.calls, "organizer 确实被调过（失败在整理这一步）"

                async with factory() as session:
                    note = await session.scalar(select(Note).where(Note.source_id == source_id))
                    assert note is None, "视频整理失败不得创建笔记"
                    job = await session.get(Job, job_id)
                    assert job.status == "failed"
                    assert "AI 整理失败" in (job.last_error or "")
                    source = await session.get(Source, source_id)
                    stages = [e["stage"] for e in source.parse_meta["video_log"]]
                    assert "AI 整理笔记中" in stages
                    # 误导元凶钉死：失败路径绝不出现「AI 整理完成」
                    assert not any("AI 整理完成" in s for s in stages)

        asyncio.run(case())

    def test_plain_file_organize_failure_degrades_to_raw(
        self, tmp_path, db_scope, fake_embed, fake_tokens
    ):
        path = _write(tmp_path, "plain.md", "\n\n".join([_PARAGRAPH] * 3))

        async def case():
            async with db_scope() as factory:
                result = await run_import_async(
                    path,
                    no_llm=False,
                    session_factory=factory,
                    embed_func=fake_embed,
                    token_counter=fake_tokens,
                    client=self._BoomLLM(),
                )
                assert result.note_id is not None
                async with factory() as session:
                    note = await session.get(Note, result.note_id)
                    headings = [
                        b for b in (note.doc or {}).get("content", []) if b.get("type") == "heading"
                    ]
                    assert not headings, "降级=原文入库,不应有 organizer 重写的标题"
                    g2 = await session.scalar(
                        select(ReviewRun).where(
                            ReviewRun.source_id == result.source_id,
                            ReviewRun.gate == "G2",
                        )
                    )
                    assert g2 is not None
                    assert any("已按原文入库" in r for r in (g2.reasons or []))

        asyncio.run(case())
