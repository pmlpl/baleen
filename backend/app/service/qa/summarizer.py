"""导入后主动总结(阶段3,D1=A):导入完成 → LLM 摘要 → 自动建「新导入:xxx」会话。

失败向上抛,由 worker **静默吞掉**(摘要失败不影响导入成功)——刻意口径,
与 answer 的「异常向上抛不降级」不同:摘要是锦上添花,不是核心链路。
LLM client 由调用方注入或按设置页(DB 优先,回退 .env)现读。
"""

from __future__ import annotations

import asyncio

from app.core.db import session_factory
from app.models.qa import QaSession
from app.repo.library import LibraryRepo
from app.repo.qa import QaRepo
from app.service.llm import LLMClient
from app.service.llm.resolve import resolve_llm_config

SUMMARY_MAX_CHARS = 8000  # 喂给摘要的笔记正文上限(父块拼接后)
DEFAULT_SUMMARY_TOKENS = 2000
SESSION_TITLE_PREFIX = "新导入:"
SESSION_TITLE_CHARS = 40


def _summary_messages(title: str, text: str) -> list[dict[str, str]]:
    system = (
        "你是个人笔记库的摘要助手。读一篇刚导入的笔记,输出:\n"
        "① 一段 200-400 字的要点摘要(这篇笔记讲什么、核心结论/方法是什么);\n"
        "② 以「建议你问:」开头,列 3 个基于这篇笔记内容、适合继续追问的具体问题。\n\n"
        "规则:只用笔记里的内容;中文;直接输出摘要正文,不要客套开头、不要解释你在做什么。"
    )
    user = f"《{title}》\n\n{text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def summarize_import(
    note_id: int, filename: str, *, client: LLMClient | None = None
) -> QaSession | None:
    """摘要 + 自动建「新导入:xxx」会话。

    - note 不存在 / 正文为空 → 返回 None(不建会话,不抛)
    - LLM 失败向上抛(worker 静默吞)
    - 测试可注入 client 与 monkeypatch session_factory / LibraryRepo 方法
    """
    async with session_factory() as session:
        repo = LibraryRepo(session, 1)
        note = await repo.get_active_note(note_id)
        if note is None:
            return None
        parents = await repo.list_parent_chunks(note_id)
        text = "\n\n".join(p.text for p in parents)[:SUMMARY_MAX_CHARS]
        base_url, api_key, model = await resolve_llm_config(session)

    if not text.strip():
        return None
    cli = client or LLMClient(base_url=base_url, api_key=api_key, model=model)
    messages = _summary_messages(note.title, text)
    chat = await asyncio.to_thread(lambda: cli.chat(messages, max_tokens=DEFAULT_SUMMARY_TOKENS))
    summary = chat.content.strip()
    if not summary:
        return None  # reasoning 模型偶发空 content → 视为失败,不建会话

    title = f"{SESSION_TITLE_PREFIX}{filename}"[:SESSION_TITLE_CHARS]
    async with session_factory() as session:
        qa_session = await QaRepo(session, 1).create_session(title)
        await QaRepo(session, 1).add_message(
            qa_session.id,
            role="assistant",
            content=summary,
            model=cli.model,
            cost_tokens=chat.total_tokens or 0,
        )
        await session.commit()
        return qa_session


def organize_conversation_messages(title: str, dialogue: str) -> list[dict[str, str]]:
    """阶段4(D2=B):会话 → 结构化 Markdown 笔记的消息组装。"""
    system = (
        "你是个人笔记库的笔记整理助手。把一段问答对话整理成一篇**结构化的 Markdown 笔记**。\n"
        "要求:\n"
        "① 提炼对话里的知识点(定义/要点/代码示例),按主题组织,不要一问一答流水账;\n"
        "② 保留代码块与关键结论,开头加一级标题(用对话主题命名)与一句概述;\n"
        "③ 只用对话里出现过的信息;④ 中文,直接输出 Markdown 正文。\n"
    )
    user = f"[对话主题]{title}\n\n[对话内容]\n{dialogue}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def organize_session_note(
    title: str, dialogue: str, *, client: LLMClient | None = None
) -> str:
    """会话 → Markdown 笔记正文(阶段4,D2=B);失败向上抛(handler 兜底提示)。"""
    cli = client or LLMClient.from_settings()
    messages = organize_conversation_messages(title, dialogue)
    chat = await asyncio.to_thread(lambda: cli.chat(messages, max_tokens=3000))
    return chat.content.strip() or "（整理失败：模型没有返回内容）"
