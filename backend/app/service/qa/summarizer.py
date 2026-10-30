"""导入后主动总结(阶段3,D1=A):导入完成 → LLM 摘要 → 自动建「新导入:xxx」会话。

失败向上抛,由 worker **静默吞掉**(摘要失败不影响导入成功)——刻意口径,
与 answer 的「异常向上抛不降级」不同:摘要是锦上添花,不是核心链路。
LLM client 由调用方注入或按设置页(DB 优先,回退 .env)现读。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

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
    note_id: int, filename: str, *, owner_id: int = 1, client: LLMClient | None = None
) -> QaSession | None:
    """摘要 + 自动建「新导入:xxx」会话。

    - note 不存在 / 正文为空 → 返回 None(不建会话,不抛)
    - LLM 失败向上抛(worker 静默吞)
    - 测试可注入 client 与 monkeypatch session_factory / LibraryRepo 方法
    """
    async with session_factory() as session:
        repo = LibraryRepo(session, owner_id)
        note = await repo.get_active_note(note_id)
        if note is None:
            return None
        parents = await repo.list_parent_chunks(note_id)
        text = "\n\n".join(p.text for p in parents)[:SUMMARY_MAX_CHARS]
        base_url, api_key, model = await resolve_llm_config(session, owner_id)

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
        qa_repo = QaRepo(session, owner_id)
        qa_session = await qa_repo.create_session(title)
        await qa_repo.add_message(
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
    title: str,
    dialogue: str,
    *,
    instruction: str = "",
    client: LLMClient | None = None,
) -> str:
    """会话 → Markdown 笔记正文(阶段4,D2=B);失败向上抛(handler 兜底提示)。

    ``instruction`` 是用户在选项卡里自由输入的补充要求(如「只保留代码部分」),
    原样拼进 system prompt;为空则与旧行为完全一致。
    """
    cli = client or LLMClient.from_settings()
    messages = organize_conversation_messages(title, dialogue)
    if instruction.strip():
        extra = instruction.strip()
        messages[0]["content"] += f"\n用户的特殊要求(优先遵守,但仍只用对话里的信息):{extra}"
    chat = await asyncio.to_thread(lambda: cli.chat(messages, max_tokens=3000))
    return chat.content.strip() or "（整理失败：模型没有返回内容）"


# ---------------- 交互式笔记:主题拆分计划(阶段4 增强) ----------------

PLAN_MAX_TOKENS = 2000
PLAN_DIALOGUE_MAX_CHARS = 12000


def plan_note_messages(title: str, dialogue: str) -> list[dict[str, str]]:
    """会话 → 主题拆分计划的消息组装(输出 JSON,不写笔记)。"""
    system = (
        "你是个人笔记库的笔记整理规划助手。读一段问答对话,判断它覆盖了几个**独立主题**,\n"
        "输出 JSON(不要解释、不要 markdown 代码块包裹):\n"
        '{"topics": [{"title": "数据库索引原理", "summary": "聊了 B+ 树结构和最左前缀",'
        ' "msg_start": 0, "msg_end": 8}], "suggestion": "split"}\n'
        "字段:\n"
        "- title:主题名(短,能当笔记标题);\n"
        "- summary:一句话概括这部分聊了什么;\n"
        "- msg_start/msg_end:该主题覆盖的消息下标 [start, end] 闭区间"
        "(0 起,整段对话按 0..N-1 编号);\n"
        "- suggestion:主题之间**基本无关**填 split,是同一个主题的深入填 single。\n"
        "规则:\n"
        "1. 区间要覆盖全部消息且不重叠(闲聊/寒暄就近归入相关主题);\n"
        "2. 单主题对话就输出一个 topic、suggestion=single;\n"
        "3. 每个主题至少要有能撑起一篇笔记的内容(几句有信息量的问答)。\n"
    )
    user = f"[对话主题]{title}\n\n[对话内容(每行前是消息下标)]\n{dialogue}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


@dataclass(frozen=True)
class NoteTopic:
    """一个可独立成篇的主题。"""

    title: str
    summary: str
    msg_start: int
    msg_end: int


@dataclass(frozen=True)
class NotePlan:
    """拆分计划:suggestion=split 表示主题间基本无关。"""

    topics: tuple[NoteTopic, ...]
    suggestion: str  # "split" | "single"


def _parse_plan(raw: str, message_count: int) -> NotePlan | None:
    """解析 LLM 的 JSON 计划;非法/越界 → None(调用方回退整段一篇)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw_topics = data.get("topics")
    suggestion = data.get("suggestion")
    if not isinstance(raw_topics, list) or not raw_topics:
        return None

    topics: list[NoteTopic] = []
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        try:
            msg_start = int(item.get("msg_start"))
            msg_end = int(item.get("msg_end"))
        except (TypeError, ValueError):
            continue
        if not title or not 0 <= msg_start <= msg_end < message_count:
            continue
        topics.append(
            NoteTopic(title=title[:60], summary=summary[:200], msg_start=msg_start, msg_end=msg_end)
        )
    if not topics:
        return None
    plan = NotePlan(topics=tuple(topics), suggestion="split" if suggestion == "split" else "single")
    return plan


async def plan_note_split(
    title: str, messages: list[dict[str, str]], *, client: LLMClient | None = None
) -> NotePlan:
    """分析会话覆盖的主题;任何失败都回退「整段一篇」——规划失败不阻塞存笔记。

    ``messages`` 是「用户/助手: 内容」之外的原始消息(role+content),
    下标即前端看到的序号;裁剪 dialogue 由调用方按 msg_start/end 做。
    """
    fallback = NotePlan(
        topics=(NoteTopic(title=title[:60], summary="", msg_start=0, msg_end=len(messages) - 1),),
        suggestion="single",
    )
    if len(messages) < 2:
        return fallback
    dialogue = "\n".join(
        f"[{i}] {'用户' if m.get('role') == 'user' else '助手'}:{m.get('content', '')}"
        for i, m in enumerate(messages)
    )[:PLAN_DIALOGUE_MAX_CHARS]
    cli = client or LLMClient.from_settings()
    chat = await asyncio.to_thread(
        lambda: cli.chat(plan_note_messages(title, dialogue), max_tokens=PLAN_MAX_TOKENS)
    )
    return _parse_plan(chat.content or "", len(messages)) or fallback


def dialogue_for_range(
    messages: list[dict[str, str]], msg_start: int, msg_end: int
) -> str:
    """按主题区间裁出对话文本(整理某一篇用,不让单篇笔记吃到全量上下文)。"""
    clamped_start = max(0, msg_start)
    clamped_end = min(len(messages) - 1, msg_end)
    picked = messages[clamped_start : clamped_end + 1]
    return "\n\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}:{m.get('content', '')}" for m in picked
    )
