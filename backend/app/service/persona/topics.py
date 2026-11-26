"""主题打标:把「我的库在讲什么」变成可统计的数据(2026-11-26 画像页)。

**为什么用 LLM 而不是关键词表**:拿真实 62 个标题试过关键词表,命中 55/62(89%),
但语义会错——「三款 AI 3D 建模工具横向测评」被 "AI" 命中归到 AI/LLM,实际是效率工具;
「温水煮青蛙:人如何一天天变平庸」这种标题关键词表完全无能为力。
主题是语义归类,必须交给 LLM。

**为什么写进 notes.tags 而不是新列**:tags 是 0002 建表就有的列,书架 ``.book-tags``
和笔记页一直在渲染它,但建库至今没有任何写入方(实测 62 篇全空)。主题打标是它
第一个真正的写入方,顺带把这两处空占位填上,不新增列。

**两阶段归纳(关键设计,别改成单阶段)**:实测单阶段分批打标时,标签不收敛——
每批是独立 LLM 调用,看不到别的批造了什么词,结果同一领域裂成三个标签:
``AI与大模型 7`` / ``AI 5`` / ``AI与智能体 5``,``系统架构 4`` / ``后端架构 3``,
14 个主题里一半是近义词。所以改成:
  ① **归纳**:把**全部**标题一次性交给 LLM,产出 ≤8 个主题名(输出短,不会烧穿预算);
  ② **打标**:拿着这个固定主题表分批给每篇打标 —— 标签集合已固定,批次间必然一致。
增量打标也复用阶段①:已有标签一并纳入归纳,新笔记只能落到已有主题上。

**增量**:只打 ``tags`` 为空的笔记。打过的跳过,标题不变就不重打(靠 tags 非空判断)。
"""

from __future__ import annotations

import asyncio
import json
import re

from app.service.llm import LLMClient

# 主题数上限:超过就失去「分布」的意义,区分度也没了
MAX_TOPICS = 8
MAX_TAGS_PER_NOTE = 2
# 一批送多少标题。**别贪大**:实测 deepseek-flash 一次 62 个标题 + max_tokens 2000
# 会 finish_reason=length 且 content 为空(推理链把预算烧光了,项目里踩过同样的坑)。
# 一次 25 个标题:输出约 25 行 JSON,稳定收尾。
BATCH_SIZE = 25
# 输出预算。deepseek-flash 带推理,2000 会烧穿 → 给 4000 留余量(仍是小成本)。
MAX_OUTPUT_TOKENS = 4000

# ── 阶段①:归纳主题表 ──

_VOCAB_SYSTEM = (
    "你是知识库主题规划助手。用户会给你一批笔记标题(编号 + 标题),"
    "请归纳出这批笔记覆盖的**主题分类**,用于后续给每篇打标签。\n\n"
    "要求:\n"
    "1. 主题数**不超过 8 个**,宁少不碎 —— 相近的合并,不要为了精确而拆成近义词"
    "(不要同时出现「AI」「AI与大模型」「AI与智能体」);\n"
    "2. 标签用中文,2-6 个字,是知识领域名(如「工程基础」「AI与大模型」「财经」「个人成长」);\n"
    "3. 覆盖所有标题:每个标题都应能归到其中一个,实在归不进去的用「其他」;\n"
    "4. 只输出 JSON 数组,形如 [\"工程基础\", \"AI与大模型\", \"财经\"],不要任何解释。\n\n"
    "注意:标题可能是视频转写来的,口语化标题也按语义归类,不要因为是口语就归到「其他」。"
)

# ── 阶段②:按固定主题表打标 ──

_TAG_SYSTEM = (
    "你是知识库主题归类助手。用户会给你**固定的主题表**和一批笔记标题(编号 + 标题),"
    "请给每篇笔记挑 1-2 个主题,第一个是最主要的。\n\n"
    "要求:\n"
    "1. 标签**必须从给定主题表里原样选**,不许自创新标签、不许改字;"
    "2. 实在归不进任何一个主题的,用主题表里的「其他」(如果没有就选最接近的);\n"
    "3. 只输出 JSON 对象,形如 {\"27\": [\"工程基础\"], \"70\": [\"工程基础\", \"Git\"]},"
    "键是笔记编号,值是标签数组。不要任何解释文字。"
)


class TopicAnalysisError(Exception):
    """打标失败(LLM 报错 / 返回格式坏)。调用方转成用户可读提示。"""


async def analyze_topics(
    notes: list[dict],
    *,
    client: LLMClient | None = None,
    known_tags: list[str] | None = None,
) -> dict[int, list[str]]:
    """两阶段打标:先归纳主题表,再逐批打标。

    ``notes``: 待打标 (id, title);``known_tags``: 已存在的标签(增量打标时传入,
    让新笔记尽量落到已有主题上,不让标签集合越滚越碎)。

    只返回**解析成功**的条目:LLM 漏掉的编号跳过(下次点按钮会补),不编造标签。
    失败向上抛 ``TopicAnalysisError``;不吞异常也不半途写库(写库在调用方)。
    """
    if not notes:
        return {}
    if client is None:
        raise TopicAnalysisError("未配置 LLM")

    vocab = await _build_vocabulary(notes, known_tags or [], client=client)
    results: dict[int, list[str]] = {}
    for start in range(0, len(notes), BATCH_SIZE):
        batch = notes[start : start + BATCH_SIZE]
        results.update(await _tag_batch(batch, vocab, client=client))

    if not results:
        raise TopicAnalysisError("主题分析没有返回可用结果")
    return results


async def _chat(client: LLMClient, system: str, user: str) -> str:
    """一次带预算护栏的调用。撞 max_tokens 明确报错,不静默当成「模型没给结果」。"""
    try:
        reply = await asyncio.to_thread(
            client.chat,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 — LLM 错误种类多,统一转成一种给前端
        raise TopicAnalysisError(f"主题分析失败:{exc}") from exc
    if reply.finish_reason == "length":
        # 实测 deepseek-flash 带推理,预算不够时 content 直接为空。
        raise TopicAnalysisError("主题分析输出被截断(模型把预算用在推理上),请重试或换非推理模型")
    return reply.content


async def _build_vocabulary(
    notes: list[dict], known_tags: list[str], *, client: LLMClient
) -> list[str]:
    """阶段①:全部标题 → ≤8 个主题名。

    已有标签一并喂进去:让模型「在既有分类上收敛」而非每次重造一套,
    否则增量打标几轮后会出现「AI」「AI与大模型」并存的老问题。
    """
    listing = "\n".join(f"{item['id']}: {item['title']}" for item in notes)
    hint = f"\n\n已有的标签(请优先沿用,只在明显不足时才新增):{json.dumps(known_tags, ensure_ascii=False)}" if known_tags else ""
    raw = await _chat(client, _VOCAB_SYSTEM + hint, f"笔记标题:\n{listing}")
    vocab = _parse_vocab(raw)
    if not vocab:
        raise TopicAnalysisError("主题分析没有返回可用结果")
    return vocab[:MAX_TOPICS]


async def _tag_batch(batch: list[dict], vocab: list[str], *, client: LLMClient) -> dict[int, list[str]]:
    """阶段②:固定主题表 → 本批每篇的标签。模型自创的标签会被 ``_parse_reply`` 过滤。"""
    listing = "\n".join(f"{item['id']}: {item['title']}" for item in batch)
    user = f"主题表:{json.dumps(vocab, ensure_ascii=False)}\n\n笔记标题:\n{listing}"
    raw = await _chat(client, _TAG_SYSTEM, user)
    return _parse_reply(raw, {item["id"] for item in batch}, allowed_tags=set(vocab))


def _parse_vocab(raw: str) -> list[str]:
    """从阶段①回复里抠 JSON 数组。容错:模型常包 ```json 围栏或加客套话。"""
    match = re.search(r"\[.*\]", raw, re.S)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str):
            name = item.strip()[:12]
            if name and name not in out:
                out.append(name)
    return out


def _parse_reply(
    raw: str, allowed_ids: set[int], allowed_tags: set[str] | None = None
) -> dict[int, list[str]]:
    """从 LLM 回复里抠 JSON 并清洗。

    容错:模型常把 JSON 包在 ```json 围栏里,或前后加客套话 —— 只抓第一个 ``{...}``。
    防御:编号必须在允许集合内(防模型编造 id);给了 ``allowed_tags`` 时
    标签必须在主题表里(防模型自创近义词,那是标签不收敛的根源)。
    """
    match = re.search(r"\{.*\}", raw, re.S)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    cleaned: dict[int, list[str]] = {}
    for key, value in data.items():
        try:
            note_id = int(key)
        except (TypeError, ValueError):
            continue
        if note_id not in allowed_ids:
            continue  # 模型幻觉出的编号,丢弃
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        tags = []
        for tag in value:
            if not isinstance(tag, str):
                continue
            name = tag.strip()[:12]
            if allowed_tags is not None:
                if name not in allowed_tags:
                    continue  # 自创标签:丢弃,让统计口径保持一致
            if name and name not in tags:
                tags.append(name)
        if tags:
            cleaned[note_id] = tags[:MAX_TAGS_PER_NOTE]
    return cleaned
