"""Charter 生成器:问卷答案 → LLM → 完整的 charter Markdown。

遵循 summarizer 模式:``client`` 由调用方注入(API 端用设置页 DB 配置),
``asyncio.to_thread`` 包装同步 ``cli.chat`` 调用。失败向上抛(handler 兜底)。
"""

from __future__ import annotations

import asyncio

from app.service.llm import LLMClient

DEFAULT_CHARTER_TOKENS = 2000

# 拿不准选项 → charter 里的自然语言描述
_UNSURE_MAP = {
    "collect_low": (
        "拿不准就收下,把 quality 标低一点。"
        "错误地拒掉一篇好笔记比错误地收下一份垃圾糟得多"
        "——收错了随时能删,拒错了根本不知道丢了什么。"
    ),
    "reject": "拿不准就拒掉。宁缺毋滥——知识库只放确定有价值的东西,模糊的不要。",
    "discuss": (
        "拿不准先别直接收也别直接扔"
        "——提出来跟库主人聊,聊完再决定要不要写成笔记。"
    ),
}


def _charter_messages(answers: dict) -> list[dict[str, str]]:
    """问卷答案 → LLM 消息(system + user),要求输出完整 charter Markdown。"""
    direction = answers.get("direction", "").strip()
    purpose = answers.get("purpose", "").strip()
    collect = answers.get("collect", [])
    reject = answers.get("reject", [])
    unsure_key = answers.get("unsure", "collect_low")
    extra = answers.get("extra", "").strip()

    unsure_text = _UNSURE_MAP.get(unsure_key, _UNSURE_MAP["collect_low"])

    collect_items = "\n".join(f"- {c}" for c in collect) if collect else "- （未选择）"
    reject_items = "\n".join(f"- {r}" for r in reject) if reject else "- （未选择）"

    system = (
        "你是个人笔记库的章程生成助手。根据用户的问卷回答,生成一份完整的知识库章程(charter)。\n"
        "章程决定 G2 归属闸怎么判——它不是技术配置,是用户自己的判断标准。\n\n"
        "输出格式(Markdown,直接输出正文,不要客套开头):\n"
        "## 这是谁的库\n（根据用户的方向和用途写一段话）\n\n"
        "## 收什么\n（根据用户选择,每条带说明和例子,用用户自己的话）\n\n"
        "## 不能收什么\n"
        "（客观无价值的:空壳文件、测试文件、壁纸表情包截图。"
        "这些不是偏好而是硬底线,写成一节直接列出,不要让用户取舍）\n\n"
        "## 不收什么\n"
        "（根据用户选择,每条带说明——这些是边界内容,收不收取决于用户的用法）\n\n"
        "## 边界怎么处理\n"
        "（默认规则:大部分是行政内容只抽技术段;"
        "整篇不像笔记但有技术话题先提出来聊;"
        "以内容为准不看文件名）\n\n"
        "## 拿不准的时候怎么办\n（根据用户选择展开）\n\n"
        "## 判据\n（默认判据:复述别人定的规则不是知识,自己踩坑想通了才是。可以适当展开）\n\n"
        "## 我自己想补的 / 想删的\n（用户的补充说明,没有就省略此段）\n\n"
        "规则:\n"
        "1. 用用户自己的话,不要照搬这个 prompt 里的措辞;\n"
        "2. 中文;\n"
        "3. 每条规则要有具体例子;\n"
        "4. 「拿不准」是★最重要的一条,要标注并放在显著位置。"
    )
    user = (
        f"[方向]{direction or '（未填）'}\n"
        f"[用途]{purpose or '（未填）'}\n"
        f"[想收什么]\n{collect_items}\n"
        f"[明确不能收(客观无价值,固定)]空文件/hello world/测试文件;"
        f"壁纸/表情包/明星照片/随手截图\n"
        f"[倾向不收(边界内容,收不收看用途)]\n{reject_items}\n"
        f"[拿不准怎么办]{unsure_text}\n"
        f"[补充]{extra or '（无）'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_charter(answers: dict, *, client: LLMClient | None = None) -> str:
    """问卷答案 → charter Markdown 文本;失败向上抛(handler 兜底提示)。

    ``answers`` 字段:direction, purpose, collect(list[str]), reject(list[str]),
    unsure("collect_low"|"reject"|"discuss"), extra(str)。
    """
    cli = client or LLMClient.from_settings()
    messages = _charter_messages(answers)
    chat = await asyncio.to_thread(lambda: cli.chat(messages, max_tokens=DEFAULT_CHARTER_TOKENS))
    return chat.content.strip() or "（生成失败：模型没有返回内容）"
