"""Charter 生成器:问卷答案 → LLM → 完整的 charter Markdown。

问卷 2026-11-26 事实化:只问事实(昵称/年龄段/性别/学历/当前状态/为什么用),
偏好全部由 LLM 从事实**推导**——旧问卷问偏好(「想收什么」「什么算好笔记」),
用户面对空库根本答不出来(主人拍板改的事实题)。

遵循 summarizer 模式:``client`` 由调用方注入(API 端用设置页 DB 配置),
``asyncio.to_thread`` 包装同步 ``cli.chat`` 调用。失败向上抛(handler 兜底)。
"""

from __future__ import annotations

import asyncio

from app.service.llm import LLMClient

DEFAULT_CHARTER_TOKENS = 2000


def _charter_messages(answers: dict) -> list[dict[str, str]]:
    """事实答案 → LLM 消息(system + user),要求输出完整 charter Markdown。"""
    nickname = str(answers.get("nickname", "")).strip()
    age_band = str(answers.get("age_band", "")).strip()
    gender = str(answers.get("gender", "")).strip()
    education = str(answers.get("education", "")).strip()
    status = str(answers.get("status", "")).strip()
    why = str(answers.get("why", "")).strip()

    system = (
        "你是个人笔记库的章程生成助手。用户只回答了几个**事实**(年龄/学历/身份/用途),"
        "你要从这些事实**推导**出一份个性化的知识库章程(charter)——"
        "一个在职程序员和一个大一学生的库该怎么不一样,是你的推导工作,不该问用户。\n\n"
        "输出格式(Markdown,直接输出正文,不要客套开头):\n"
        "## 这是谁的库\n"
        "（结合年龄/学历/身份/用途写一段话,把画像讲清楚;有昵称就用昵称称呼）\n\n"
        "## 收什么\n"
        "（由身份+用途推导这个库该收什么,每条带说明和一个具体例子;"
        "推导不出方向就写通用的:概念笔记、踩坑记录、可复现的问题解决方案）\n\n"
        "## 不能收什么\n"
        "（客观无价值的:空壳文件、测试文件、壁纸表情包截图。"
        "这些不是偏好而是硬底线,直接列出,不要让用户取舍）\n\n"
        "## 不收什么\n"
        "（由身份推导的边界内容,每条带说明——推导不出就省略这一段）\n\n"
        "## 笔记长什么样\n"
        "（五条骨架:①用自己的话写不摘抄;②一节讲透一个要点;"
        "③概念给定义+可运行例子;④问题记录三段齐全(现象-原因-解决)且可复现;"
        "⑤二手材料带出处、自己的理解和二手分开。"
        "按身份调整口吻与深度:学生多给入门定义和例子,在职多给行业口径和实操细节,"
        "每条带一个例子）\n\n"
        "## 拿不准的时候怎么办\n"
        "（固定写这条,不问用户:拿不准就收下,把 quality 标低一点——"
        "错误地拒掉一篇好笔记比错误地收下一份垃圾糟得多,"
        "收错了随时能删,拒错了根本不知道丢了什么。标注为最重要的一条,放显著位置）\n\n"
        "## 判据\n"
        "（默认判据:复述别人定的规则、走流程、按模板汇报不是知识;"
        "自己踩坑想通了为什么、给出可复现解法才是。展开成可操作的判断句）\n\n"
        "规则:\n"
        "1. 用用户自己的话,不要照搬这个 prompt 里的措辞;\n"
        "2. 中文;\n"
        "3. 每条规则要有具体例子;\n"
        "4. 事实没答的题,按通用情况推导,别硬编;\n"
        "5. 章程是给「整理笔记的 AI」看的,规则要能被 LLM 执行,不是给人看的口号。"
    )
    user = (
        f"[怎么称呼你]{nickname or '（未填）'}\n"
        f"[年龄段]{age_band or '（未填）'}\n"
        f"[性别]{gender or '（未填）'}\n"
        f"[学历]{education or '（未填）'}\n"
        f"[当前状态]{status or '（未填）'}\n"
        f"[为什么用 baleen]{why or '（未填）'}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_charter(answers: dict, *, client: LLMClient | None = None) -> str:
    """问卷答案(事实) → charter Markdown 文本;失败向上抛(handler 兜底提示)。

    ``answers`` 字段(2026-11-26 事实化):nickname, age_band, gender, education,
    status, why——全部可空。
    """
    cli = client or LLMClient.from_settings()
    messages = _charter_messages(answers)
    chat = await asyncio.to_thread(lambda: cli.chat(messages, max_tokens=DEFAULT_CHARTER_TOKENS))
    return chat.content.strip() or "（生成失败：模型没有返回内容）"
