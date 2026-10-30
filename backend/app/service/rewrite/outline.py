"""标题结构二次加工:让 LLM 补/改标题,编号由代码确定性生成。

为什么不直接让 LLM 输出带编号的标题:它会数错、跳号、层级乱。
所以拆成两步 ——

1. LLM 只出「在第 N 个块之前插一个 L 级标题,叫某某」这类**结构计划**;
2. Python 按层级关系算编号(``一、`` / ``2.1 `` / ``2.1.3 ``),永远自洽。

只动标题,正文一个字不改(避免 AI 篡改原文)。
产物是新的 block JSON —— 切块只看 block JSON(chunker.py 只认 type=="heading"),markdown 无意义。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from app.core.config import get_settings
from app.service.llm import LLMClient
from app.service.parse.blocks import heading as heading_block

# 输出预算按模型上限跑满(reasoning 也计入,给小了正文会整条为空)。
# 取值见 config.llm_max_output_tokens,可用 LLM_MAX_OUTPUT_TOKENS 覆盖。
DEFAULT_MAX_TOKENS = get_settings().llm_max_output_tokens
# 摘要里每块最多取多少字符(长段落只给开头,省 token)
BLOCK_PREVIEW_CHARS = 120
# 单次喂给 LLM 的块数上限。超长文档**分段**调用,不截断丢内容 ——
# 一段整理不下的笔记,后面的段落各自单独整理,最后按 before_index 合并。
CHUNK_BLOCKS = 120
# 正文字符数低于这个值就不值得整理
MIN_REWRITE_CHARS = 400
# 章节「空」的判定:没有子标题,且自身正文少于这么多字。
# 取 100:一段正常的技术说明通常两三行就超过;只有「一行导语就跳到下一章」才算空。
# (定太高会误伤正常的短末级小节)
EMPTY_SECTION_CHARS = 100
# 认为「已经有编号」的前缀:一、/ 2.1 / 第 3 章 等
NUMBER_PREFIX = re.compile(
    r"^\s*(?:[一二三四五六七八九十百]+[、.．]|第[一二三四五六七八九十百\d]+[章节部分]|\d+(?:\.\d+)*[、.．\s])"
)

CN_NUMBERS = "一二三四五六七八九十"


@dataclass(frozen=True)
class HeadingSpec:
    """一条标题计划(编号前)。"""

    before_index: int  # 插到 doc["content"][before_index] 之前
    level: int  # 1..6
    title: str  # 只含语义部分,不含编号

    def numbered(self, prefix: str) -> HeadingSpec:
        return replace(self, title=f"{prefix}{self.title}")


def _block_text(block: dict) -> str:
    """取块里的纯文本(递归收集 text 节点)。"""
    parts: list[str] = []

    def walk(node: dict) -> None:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        for child in node.get("content") or []:
            walk(child)

    walk(block)
    return "".join(parts).strip()


def _outline_messages(
    title: str,
    blocks: list[dict],
    *,
    offset: int = 0,
    total: int | None = None,
) -> list[dict[str, str]]:
    """把 doc 摊平成带序号的摘要,要求 LLM 输出 JSON 结构计划。

    ``offset`` 是本段在原文档里的起始块号(分段整理时用),让 LLM 看到的序号
    跟全局一致,产出的 ``at`` 可直接当作全局索引。``total`` 仅用于提示这是节选。
    """
    lines: list[str] = []
    for local_i, block in enumerate(blocks):
        i = local_i + offset
        btype = block.get("type", "?")
        text = _block_text(block)
        preview = text[:BLOCK_PREVIEW_CHARS].replace("\n", " ")
        if btype == "heading":
            level = (block.get("attrs") or {}).get("level", 1)
            lines.append(f"[{i}] heading(L{level}): {preview}")
        elif btype in ("codeBlock", "table", "image", "horizontalRule"):
            lines.append(f"[{i}] {btype}")
        elif preview:
            lines.append(f"[{i}] {btype}: {preview}")

    system = (
        "你是文档结构整理助手。给定一篇文档的块列表(带序号与类型),\n"
        "判断它的标题结构应该怎样,输出一份**标题计划**。\n\n"
        "输出格式:只输出 JSON 数组,不要解释、不要 markdown 代码块包裹:\n"
        '[{"at": 3, "level": 2, "title": "官方定义"},\n'
        ' {"at": 74, "level": 2, "title": "fetch 适用的场景"}]\n\n'
        "字段含义:\n"
        "- at:插在第几个块**之前**(用上面列表里的序号)\n"
        "- level:标题级别,1=章,2=节,3=小节\n"
        "- title:**只要语义标题,绝对不要写编号**\n"
        "  (写「fetch 适用的场景」,不要写「8.1 fetch 适用的场景」)\n\n"
        "规则:\n"
        "1. 编号程序会自动加,你写了反而会重复;\n"
        "2. 已有的好标题也要**原样给出一条**(不带编号部分),否则那个位置不会生成标题;\n"
        "3. 补上缺失的标题 —— 尤其是一大段连续正文没有标题时,根据内容起一个;\n"
        "4. **同级标题不要重名**。同一父章节下若有多个相似小节,措辞要能区分\n"
        "   (如「fetch 处理错误的写法」「axios 处理错误的写法」而不是两个都叫「错误处理」);\n"
        "5. 层级要合理:章下面才有节,不要跳级;\n"
        "6. 别太碎:同一主题的段落归到一个标题下,不要每段都加标题。"
    )
    user = f"[文档标题]{title}\n\n[块列表]\n" + "\n".join(lines)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_specs(raw: str, block_count: int) -> list[HeadingSpec]:
    """解析 LLM 的 JSON 数组;容错(去掉代码围栏、坏条目跳过)。"""
    text = raw.strip()
    # 去掉可能的 ```json ... ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 只取第一个 [ ... ] 区间,防止模型加解释
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    specs: list[HeadingSpec] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            at = int(item.get("at"))
            level = int(item.get("level", 2))
        except (TypeError, ValueError):
            continue
        title = str(item.get("title", "")).strip()
        if not title or title.isdigit():
            continue
        if not 0 <= at < block_count:
            continue  # 越界丢弃
        level = min(max(level, 1), 6)
        specs.append(HeadingSpec(before_index=at, level=level, title=title))
    # 按位置排序;同位置按 level 升序(父标题在子标题前)
    specs.sort(key=lambda s: (s.before_index, s.level))
    return specs


# 结构化输出比问答慢(推理模型要先把整篇过一遍),60s 默认超时不够
REWRITE_TIMEOUT = 180.0


def plan_outline(
    blocks: dict,
    *,
    title: str,
    client: LLMClient,
    max_tokens: int | None = None,
) -> list[HeadingSpec]:
    """调 LLM 产出标题计划(未编号)。失败向上抛(流水线会吞掉并保留原结构)。

    超长文档**分段处理**:每 CHUNK_BLOCKS 块一段,逐段问 LLM,最后合并结果。
    不做截断 —— 尾部内容也有机会补上标题。段与段之间不重叠,索引按全局偏移换算。

    超时由 config.llm_long_task_timeout 控制(不是单次问答的 60s)。
    """
    settings = get_settings()
    budget = max_tokens or settings.llm_max_output_tokens
    content = blocks.get("content") or []
    if not content:
        return []

    specs: list[HeadingSpec] = []
    total = len(content)
    for start in range(0, total, CHUNK_BLOCKS):
        window = content[start : start + CHUNK_BLOCKS]
        messages = _outline_messages(title, window, offset=start, total=total)
        chat = client.chat(messages, max_tokens=budget)
        # 段内索引 → 全局索引
        for spec in _parse_specs(chat.content or "", len(window)):
            specs.append(
                HeadingSpec(
                    before_index=spec.before_index + start,
                    level=spec.level,
                    title=spec.title,
                )
            )
    specs.sort(key=lambda s: (s.before_index, s.level))
    return specs


def number_headings(specs: list[HeadingSpec]) -> list[HeadingSpec]:
    """按层级关系给标题加确定性编号。

    一级用「一、二、三」;二级起用「2.1」「2.1.3」。
    进入更深层级时重置其子计数器,保证 ``2.1 -> 2.1.1 -> 2.2`` 正确。
    """
    counters = [0] * 7
    numbered: list[HeadingSpec] = []
    for spec in sorted(specs, key=lambda s: (s.before_index, s.level)):
        level = min(max(spec.level, 1), 6)
        counters[level] += 1
        for deeper in range(level + 1, 7):
            counters[deeper] = 0

        if level == 1:
            n = counters[1]
            prefix = (CN_NUMBERS[n - 1] if 1 <= n <= len(CN_NUMBERS) else str(n)) + "、"
        else:
            # 父级计数为 0(该级之前没出现过上一级)→ 兜底成 1,避免出现「0.1」
            parts = []
            for depth in range(1, level + 1):
                parts.append(str(counters[depth] if counters[depth] > 0 else 1))
            prefix = ".".join(parts) + " "
        numbered.append(spec.numbered(prefix))
    return numbered


def apply_outline(blocks: dict, specs: list[HeadingSpec]) -> dict:
    """把标题计划插进 doc,返回**新的** doc(不修改入参)。

    插在目标块之前;同一位置多条按传入顺序(已按 level 排序)依次插入。
    """
    content = list(blocks.get("content") or [])
    if not specs:
        return {"type": "doc", "content": content}

    # 按 before_index 分组,倒序插入,避免索引偏移
    grouped: dict[int, list[HeadingSpec]] = {}
    for spec in specs:
        if not 0 <= spec.before_index <= len(content):
            continue
        if not spec.title.strip():
            continue
        grouped.setdefault(spec.before_index, []).append(spec)

    for index in sorted(grouped, reverse=True):
        new_nodes = [
            heading_block(min(max(s.level, 1), 6), s.title.strip()) for s in grouped[index]
        ]
        content[index:index] = new_nodes

    return {"type": "doc", "content": content}


def needs_rewrite(blocks: dict) -> bool:
    """要不要花这次 LLM 调用 —— 廉价启发式,只判明显需要改的。

    返回 False 的情况(结构已经不错 / 不值得):
    - 正文太短(< MIN_REWRITE_CHARS)
    - 有 ≥2 个标题、无同级重名、且 80% 以上已带编号
    """
    content = blocks.get("content") or []
    if not content:
        return False

    # 正文字符数(不含标题自身)
    total_chars = 0
    headings: list[tuple[int, str]] = []
    for block in content:
        if block.get("type") == "heading":
            level = int((block.get("attrs") or {}).get("level", 1))
            headings.append((level, _block_text(block)))
        else:
            total_chars += len(_block_text(block))
    if total_chars < MIN_REWRITE_CHARS:
        return False

    # 完全没标题 → 一定需要
    if not headings:
        return True
    # 只有 1 个标题且正文很长 → 需要分节
    if len(headings) == 1:
        return True

    # 同级重名检测
    seen: dict[tuple[int, str], int] = {}
    duplicate = False
    for level, text in headings:
        key = (level, text.strip())
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            duplicate = True
            break
    if duplicate:
        return True

    # 空章节检测:某标题下面既没有更深一层的标题,也没有足够正文 → 该补子标题
    # (遍历 blocks 而不是只看标题列表,才能知道每个标题占了多少正文)
    if _has_empty_section(content):
        return True

    # 编号覆盖率:80% 以上带编号就算合格
    numbered = sum(1 for _lvl, text in headings if NUMBER_PREFIX.match(text))
    return numbered / len(headings) < 0.8


def _has_empty_section(content: list[dict]) -> bool:
    """有标题下面既无子标题、正文也少 → 结构不完整,值得让 AI 补。

    只看「章」(level 最小的那层)之后的正文:章标题后面若直接跟着下一个同级
    或更高级标题,中间没有内容也没有子节,就是空壳章。
    """
    if not content:
        return False

    # 记录每个标题块:层级、自己那段正文的字数、是否有更深的子标题
    marks: list[dict] = []
    for block in content:
        if block.get("type") == "heading":
            level = int((block.get("attrs") or {}).get("level", 1))
            marks.append({"level": level, "chars": 0, "has_child": False})
        elif marks:
            marks[-1]["chars"] += len(_block_text(block))

    for i, mark in enumerate(marks):
        for later in marks[i + 1 :]:
            if later["level"] > mark["level"]:
                mark["has_child"] = True
                break
            if later["level"] <= mark["level"]:
                break  # 遇到同级/更高级,子节范围结束

    # 「空」= 既没有子标题,自己那段正文也几乎为零。
    # 末级标题天然没有子标题 —— 只要正文达到阈值就不算空(否则每个叶子都会被误判)。
    return any(m["has_child"] is False and m["chars"] < EMPTY_SECTION_CHARS for m in marks)


def summarize_specs(specs: list[HeadingSpec]) -> list[dict]:
    """给日志/结果用的轻量视图。"""
    return [{"at": s.before_index, "level": s.level, "title": s.title} for s in specs]


def doc_stats(blocks: dict) -> dict:
    """轻量结构统计(标题数/带编号数/正文字数),供回填 dry-run 解释「为什么跳过」。"""
    headings = 0
    numbered = 0
    body_chars = 0
    for block in blocks.get("content") or []:
        if block.get("type") == "heading":
            headings += 1
            if NUMBER_PREFIX.match(_block_text(block)):
                numbered += 1
        else:
            body_chars += len(_block_text(block))
    return {"headings": headings, "numbered": numbered, "body_chars": body_chars}


__all__ = [
    "HeadingSpec",
    "apply_outline",
    "doc_stats",
    "needs_rewrite",
    "number_headings",
    "plan_outline",
    "summarize_specs",
]
