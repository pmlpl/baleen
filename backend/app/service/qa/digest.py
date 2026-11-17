"""对话 digest:把若干早期消息冻结成一段不可变摘要,作为 LLM 历史的压缩视角。

为什么需要它:chat 路径原本每轮把整条历史原样重放(user 消息按 llm_content 重放,
换 DeepSeek 前缀缓存命中 90%+),但超长会话会静默硬截断丢上下文、且成本二次式增长。
这里做「冻结分段摘要」——每隔 K 轮把最老的几轮压成一段**不可变**摘要,放进稳定前缀;
近 K 轮仍 append-only 原样重放。两次冻结之间前缀字节不变 → 缓存命中保留;冻结时
只付一次 cache-write。

被 digest 覆盖的旧消息**不删**(qa_messages 表不动,前端仍显示完整对话原文);
digest 只是「喂 LLM 时」的内部压缩视角。详见 answerer.compose 的消息结构。
"""

from __future__ import annotations

import asyncio

from app.service.llm import LLMClient, estimate_tokens

# 单段摘要输出上限。摘要要紧凑(替后续轮次压缩历史 token),不是给人读的成品笔记。
DIGEST_MAX_TOKENS = 800
# 冻结触发:近窗 token 超此值才冻结(不按条数 —— 短对话不浪费 cache-write,长对话才压)。
# 1M 上下文下设高,日常对话不触发;真正超长会话才冻结。缓存命中是瓶颈(22K 验证 90%),
# 若超长前缀命中率下降,回调此值到"命中率拐点"之前。
DIGEST_TRIGGER_TOKENS = 100000
# 每次冻结多少条(= 5 轮)压成一段 digest。
DIGEST_FREEZE_BATCH = 10
# 喂给摘要器的对话文本上限(超则从最旧截;摘要看重近期,老的可丢)。
DIGEST_DIALOGUE_MAX_CHARS = 16000
# digest 段总 token 兜底:累积多段后超此值从最旧段丢。
# Tier-1 兜底(真正无限长靠 Tier-2 检索式记忆,复用现有 RAG 向量库基建,本次不做)。
DIGEST_TOTAL_BUDGET = 6000


def build_digest_messages(dialogue: str) -> list[dict[str, str]]:
    """组装摘要器消息:system 契约 + user 对话文本。

    与 ``summarizer.organize_conversation_messages`` 的区别:digest 不是给人读的成品
    笔记,而是喂回 LLM 当历史用的压缩视角 —— 第三人称客观、保核心问题/结论/用户偏好
    约束,丢检索片段原文与寒暄,不用 Markdown 标题/编号,严格控长。
    """
    system = (
        "你是对话摘要器。把下面几轮问答压成一段紧凑摘要,供后续对话作为历史背景。\n"
        "要求:\n"
        "① 保留:用户问的核心问题、AI 给的关键结论与事实、涉及的笔记/主题、"
        "用户明确表达的偏好或约束(如「以后都按 Markdown 输出」「只关心性能」);\n"
        "② 丢弃:寒暄、重复、检索片段的原文、无关细节;\n"
        "③ 用第三人称客观陈述(「用户询问了…」「结论是…」),不要一问一答流水账;\n"
        "④ 中文,纯文本,不要 Markdown 标题、不要 [n] 编号、不要代码块;\n"
        "⑤ 不超过 400 字。\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"[待摘要对话]\n{dialogue}"},
    ]


def dialogue_from_messages(msgs: list) -> str:
    """把 QaMessage 列表拍平成 ``用户:…\\n助手:…`` 文本。

    用 ``content`` 人可读版,不用 ``llm_content`` —— 摘要不需要旧检索片段原文(那是
    answerer 重放的事),省 token 且避免摘要被超长 <context> 撑爆。
    超 ``DIGEST_DIALOGUE_MAX_CHARS`` 时从最旧截(摘要看重近期)。
    """
    text = "\n".join(
        f"{'用户' if m.role == 'user' else '助手'}:{m.content}" for m in msgs
    )
    if len(text) > DIGEST_DIALOGUE_MAX_CHARS:
        # 保留最新的部分:从尾部取
        text = text[-DIGEST_DIALOGUE_MAX_CHARS:]
    return text


async def run_digest(dialogue: str, *, client: LLMClient | None = None) -> str:
    """调 LLM 生成一段摘要文本;失败向上抛(由 worker 决定重试/丢弃)。

    不做续写(摘要目标是压缩,超 max_tokens 就标截断,不像整理笔记那样续写拼回)。
    返回空串表示模型没产出内容(调用方应跳过落库)。
    """
    cli = client or LLMClient.from_settings()
    messages = build_digest_messages(dialogue)
    chat = await asyncio.to_thread(lambda: cli.chat(messages, max_tokens=DIGEST_MAX_TOKENS))
    body = (chat.content or "").strip()
    if not body:
        return ""
    if chat.finish_reason == "length":
        body += "\n(摘要因长度上限被截断)"
    return body


def compose_digest_block(summaries: list[str]) -> str:
    """多段不可变摘要 → ``<prior_summary>`` 块(作为第二条 system message 的内容)。

    旧段字节不变是前缀缓存命中的前提;只在「新增一段」时付 cache-write,之后长期命中。
    超 ``DIGEST_TOTAL_BUDGET`` 时从最旧段开始丢(Tier-1 兜底;分层摘要留 Tier-2)。
    返回空串 = 无 digest,调用方据此跳过第二条 system(行为同改造前)。
    """
    parts = [s for s in summaries if s]
    if not parts:
        return ""
    # 从最新往旧累加,超预算就停 → 保最新的若干段,丢最旧的;
    # 最新一段保底留下(即使它本身就超预算,也比全丢好)
    kept: list[str] = []
    total = 0
    for s in reversed(parts):
        t = estimate_tokens(s)
        if kept and total + t > DIGEST_TOTAL_BUDGET:
            break
        kept.append(s)
        total += t
    kept.reverse()
    body = "\n\n".join(kept)
    if len(kept) < len(parts):
        body = "(更早的对话摘要因篇幅已省略)\n\n" + body
    return f"<prior_summary>\n{body}\n</prior_summary>"
