"""organizer.py 单测：mock LLMClient，不联网。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.service.llm.client import LLMError
from app.service.parse.types import ParseResult, ParseStatus
from app.service.rewrite.organizer import (
    organize_note,
    renumber_headings_in_blocks,
)

# ---- fake LLM client ----


class FakeClient:
    """假 LLMClient：按预设返回 content，记录调用次数。"""

    def __init__(
        self,
        responses: list[str],
        *,
        model: str = "fake-model",
        fail_with: type[Exception] | None = None,
    ):
        self._responses = list(responses)
        self.model = model
        self.fail_with = fail_with
        self.call_count = 0
        self.received_messages: list[list[dict]] = []
        self.received_max_tokens: list[int] = []

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
        response_format: Any = None,
    ) -> Any:
        self.received_messages.append(messages)
        self.received_max_tokens.append(max_tokens)
        self.call_count += 1
        if self.fail_with is not None:
            raise self.fail_with("fake network error")
        content = self._responses.pop(0) if self._responses else ""
        return SimpleNamespace(content=content, total_tokens=100, finish_reason="stop")


def _make_parse_result(text: str = "这是一段关于闭包的技术内容。") -> ParseResult:
    blocks = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return ParseResult(parse_status=ParseStatus.OK, blocks=blocks, meta={"chars": len(text)})


_SUCCESS_JSON = (
    '{"organizable": true, "markdown": "# 闭包详解\\n\\n## 闭包的定义'
    '\\n\\n闭包是函数和词法环境的组合。", '
    '"quality": "core", "note_type": "concept", "reasons": ["提取了技术内容"]}'
)

_NOT_ORGANIZABLE_JSON = (
    '{"organizable": false, "markdown": "", "quality": "junk", "note_type": "ad", '
    '"reasons": ["全文是课程推广，无技术内容"]}'
)


# ---- renumber_headings_in_blocks ----


def _heading(level: int, text: str) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_renumber_simple() -> None:
    doc = {
        "type": "doc",
        "content": [
            _heading(1, "什么是闭包"),
            _para("闭包是..."),
            _heading(2, "形成条件"),
            _para("需要嵌套..."),
            _heading(2, "应用场景"),
            _para("用于回调..."),
        ],
    }
    result = renumber_headings_in_blocks(doc)
    headings = [b for b in result["content"] if b["type"] == "heading"]
    assert headings[0]["content"][0]["text"] == "一、什么是闭包"
    assert headings[1]["content"][0]["text"] == "1.1 形成条件"
    assert headings[2]["content"][0]["text"] == "1.2 应用场景"


def test_renumber_strips_existing_numbers() -> None:
    """LLM 可能加编号，strip 后重新编号。"""
    doc = {
        "type": "doc",
        "content": [
            _heading(1, "一、闭包基础"),
            _para("内容"),
            _heading(2, "2.1 定义"),
            _para("内容"),
        ],
    }
    result = renumber_headings_in_blocks(doc)
    headings = [b for b in result["content"] if b["type"] == "heading"]
    assert headings[0]["content"][0]["text"] == "一、闭包基础"
    assert headings[1]["content"][0]["text"] == "1.1 定义"


def test_renumber_multi_level() -> None:
    doc = {
        "type": "doc",
        "content": [
            _heading(1, "闭包基础"),
            _heading(2, "定义与原理"),
            _heading(3, "词法作用域"),
            _heading(3, "变量捕获"),
            _heading(2, "应用场景"),
            _heading(1, "闭包进阶"),
        ],
    }
    result = renumber_headings_in_blocks(doc)
    headings = [b["content"][0]["text"] for b in result["content"] if b["type"] == "heading"]
    assert headings == [
        "一、闭包基础",
        "1.1 定义与原理",
        "1.1.1 词法作用域",
        "1.1.2 变量捕获",
        "1.2 应用场景",
        "二、闭包进阶",
    ]


def test_renumber_no_headings() -> None:
    doc = {"type": "doc", "content": [_para("纯文本无标题")]}
    result = renumber_headings_in_blocks(doc)
    assert result == doc


# ---- organize_note ----


def test_organize_success() -> None:
    client = FakeClient([_SUCCESS_JSON])
    pr = _make_parse_result("闭包是函数和词法环境的组合。闭包可以用于回调。")
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True
    assert result.blocks is not None
    assert result.quality == "core"
    assert result.note_type == "concept"
    assert "提取了技术内容" in result.reasons
    assert result.cost_tokens == 100
    assert result.model == "fake-model"
    assert client.call_count == 1
    # blocks 有标题
    headings = [b for b in result.blocks["content"] if b["type"] == "heading"]
    assert len(headings) >= 1
    # 标题带编号
    assert headings[0]["content"][0]["text"].startswith("一、")


def test_organize_not_organizable() -> None:
    client = FakeClient([_NOT_ORGANIZABLE_JSON])
    pr = _make_parse_result("关注公众号获取更多资源！一键三连！")
    result = organize_note(pr, filename="ad.md", ext=".md", client=client)

    assert result.organizable is False
    assert result.blocks is None
    assert result.quality == "junk"
    assert "课程推广" in result.reasons[0]


def test_organize_json_retry_then_success() -> None:
    bad_json = "这不是JSON"
    client = FakeClient([bad_json, _SUCCESS_JSON])
    pr = _make_parse_result()
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True
    assert result.blocks is not None
    assert client.call_count == 2  # 第一次坏，第二次好


def test_organize_degraded_both_json_bad() -> None:
    client = FakeClient(["坏JSON1", "坏JSON2"])
    pr = _make_parse_result("有内容但LLM输出不可解析。")
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True  # 降级：原文入库
    assert result.blocks is not None
    assert result.blocks == pr.blocks  # 原文 blocks
    assert result.meta.get("degraded") is True
    assert client.call_count == 2


def test_organize_llm_error_raises() -> None:
    """LLMError 向上抛（pipeline 层降级）。"""
    client = FakeClient([], fail_with=LLMError)
    pr = _make_parse_result()
    with pytest.raises(LLMError):
        organize_note(pr, filename="test.md", ext=".md", client=client)


def test_organize_organizable_but_empty_markdown_degrades() -> None:
    json_empty_md = (
        '{"organizable": true, "markdown": "", '
        '"quality": "normal", "note_type": "other", "reasons": []}'
    )
    client = FakeClient([json_empty_md])
    pr = _make_parse_result()
    result = organize_note(pr, filename="test.md", ext=".md", client=client)

    assert result.organizable is True  # 降级入库
    assert result.blocks == pr.blocks  # 原文
    assert result.meta.get("degraded") is True


def test_organize_prompt_contains_contract() -> None:
    """system prompt 包含 note-format.md 关键规则。"""
    client = FakeClient([_SUCCESS_JSON])
    pr = _make_parse_result()
    organize_note(pr, filename="test.md", ext=".md", client=client)

    system_msg = client.received_messages[0][0]["content"]
    assert "标题" in system_msg
    assert "编号" in system_msg
    assert "代码块" in system_msg
    assert "表格" in system_msg
    assert "内容骨架" in system_msg or "一节讲透" in system_msg
    # quality 评级标准：评整理后的笔记本身，视频/字幕来源不构成降级理由
    assert "评的是你整理出的这篇笔记本身" in system_msg
    assert "不构成降级理由" in system_msg
    assert "知识点完整闭环" in system_msg
    # 多并列大主题 → 多章结构指引（硬规则 + 大纲示例 + 单/多章判断标准）
    assert "并列的大主题" in system_msg
    assert "多章结构" in system_msg
    assert "硬性规则" in system_msg
    assert "正确示例" in system_msg
    assert "错误示例" in system_msg
    assert "去掉任何一个，其余仍独立成立" in system_msg
    assert "<file_content>" in client.received_messages[0][1]["content"]


def test_organize_content_wrapped_in_file_content_tag() -> None:
    """正文包在 <file_content> 里防注入。"""
    client = FakeClient([_SUCCESS_JSON])
    pr = _make_parse_result("敏感内容")
    organize_note(pr, filename="test.md", ext=".md", client=client)

    user_msg = client.received_messages[0][1]["content"]
    assert "<file_content>" in user_msg
    assert "</file_content>" in user_msg
    assert "敏感内容" in user_msg


# ---- 预算自适应(prompt_budget / output_budget)----


def test_organize_large_doc_not_truncated_when_no_budget() -> None:
    """prompt_budget=None(用户没填 context)→ 全文喂,不截断。

    这是修视频/长文档导入截断的核心断言:旧 PROMPT_TOKEN_BUDGET=6000 会把这种长度截断。
    """
    big_text = "闭包是函数和词法环境的组合。" * 4000  # 远超旧 6000 token 上限
    pr = _make_parse_result(big_text)
    client = FakeClient([_SUCCESS_JSON])
    result = organize_note(pr, filename="big.md", ext=".md", client=client)

    user_msg = client.received_messages[0][1]["content"]
    assert "已截断" not in user_msg
    assert big_text in user_msg  # 全文都在
    assert result.meta.get("truncated") is False


def test_organize_large_doc_truncated_when_budget_set() -> None:
    """prompt_budget=6000(用户填了 context)→ 按值截断(保留旧行为,供精确控制)。"""
    big_text = "闭包是函数和词法环境的组合。" * 4000
    pr = _make_parse_result(big_text)
    client = FakeClient([_SUCCESS_JSON])
    organize_note(pr, filename="big.md", ext=".md", client=client, prompt_budget=6000)

    user_msg = client.received_messages[0][1]["content"]
    assert "已截断" in user_msg
    assert big_text not in user_msg  # 被截断了


def test_organize_output_default_when_none() -> None:
    """output_budget=None(用户没填)→ 用 ORGANIZER_DEFAULT_OUTPUT_TOKENS。"""
    from app.service.rewrite.organizer import ORGANIZER_DEFAULT_OUTPUT_TOKENS

    pr = _make_parse_result()
    client = FakeClient([_SUCCESS_JSON])
    organize_note(pr, filename="test.md", ext=".md", client=client)
    assert client.received_max_tokens[0] == ORGANIZER_DEFAULT_OUTPUT_TOKENS


def test_organize_output_override() -> None:
    """output_budget=5000 → 用 5000(用户填的覆盖兜底)。"""
    pr = _make_parse_result()
    client = FakeClient([_SUCCESS_JSON])
    organize_note(pr, filename="test.md", ext=".md", client=client, output_budget=5000)
    assert client.received_max_tokens[0] == 5000


def test_organize_max_tokens_priority() -> None:
    """max_tokens(测试直传)优先于 output_budget。"""
    pr = _make_parse_result()
    client = FakeClient([_SUCCESS_JSON])
    organize_note(
        pr, filename="test.md", ext=".md", client=client, max_tokens=999, output_budget=5000
    )
    assert client.received_max_tokens[0] == 999


# ---- 视频多模态模式（阶段2）----

_VIDEO_MD = (
    "# 视频笔记\n\n"
    "讲者说了一段话。\n\n"
    "![[0:40] 讲者画面](baleen-image:0)\n\n"
    "然后展示了白板。\n\n"
    "![[1:46] tldraw白板](baleen-image:1)\n"
)

_VIDEO_SUCCESS_JSON = (
    '{"organizable": true, "markdown": "# 信任与智能体\\n\\n## 讲者观点\\n\\n'
    "讲者认为 AI 是结对伙伴。\\n\\n![[0:40] 讲者画面](baleen-image:0)\\n\\n"
    '## 白板示意\\n\\n白板展示了 trust 曲线。\\n\\n![[1:46] tldraw白板](baleen-image:1)", '
    '"quality": "core", "note_type": "concept", "reasons": ["融合语音与画面"]}'
)


def _make_video_images(tmp_path, count: int = 2) -> list[dict]:
    """造真实 media 文件 + 帧清单（走 media_path 真读，不用 mock）。"""
    import hashlib

    from PIL import Image

    from app.service import media as media_module

    out = []
    for i in range(count):
        # 生成一张小图 → 按 store_media 的存储布局直接落到 media_root（测试隔离目录）
        img = Image.new("RGB", (32, 32), (i * 80, 0, 0))
        p = tmp_path / f"frame_{i}.jpg"
        img.save(p)
        data = p.read_bytes()
        key = hashlib.sha256(data).hexdigest()
        target = media_module.MEDIA_ROOT / key[:2] / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out.append(
            {
                "index": i,
                "media_key": key,
                "alt": f"画面{i}",
                "text_layer": f"code_{i}()",
                "timestamp": i * 60.0,
            }
        )
    return out


def test_organize_video_sends_multimodal_message(tmp_path, monkeypatch) -> None:
    """视频模式：user 消息是 list（text + image_url），system 带视频专属规则。"""
    from app.service import media as media_module

    monkeypatch.setattr(media_module, "MEDIA_ROOT", tmp_path / "media")
    video_images = _make_video_images(tmp_path)

    pr = _make_parse_result()
    client = FakeClient([_VIDEO_SUCCESS_JSON])
    organize_note(pr, filename="v.md", ext=".md", client=client, video_images=video_images)

    msgs = client.received_messages[0]
    assert "视频笔记专属规则" in msgs[0]["content"]
    assert "baleen-image:N" in msgs[0]["content"]  # 视频规则里提到标记格式
    # user 消息是 list：text + 2 张图
    user_c = msgs[1]["content"]
    assert isinstance(user_c, list)
    assert user_c[0]["type"] == "text"
    assert "baleen-image:0" in user_c[0]["text"]  # 标记→画面对照表
    assert "画面0" in user_c[0]["text"]
    image_parts = [p for p in user_c if p.get("type") == "image_url"]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_organize_video_restores_image_blocks(tmp_path, monkeypatch) -> None:
    """视频模式：organizer 输出里的 baleen-image:N → 还原成带 media key 的 image 块。"""
    from app.service import media as media_module

    monkeypatch.setattr(media_module, "MEDIA_ROOT", tmp_path / "media")
    video_images = _make_video_images(tmp_path)

    md_with_images = (
        "# 视频笔记\n\n讲者观点。\n\n![[0:40] 讲者](baleen-image:0)\n\n"
        "白板内容。\n\n![[1:46] 白板](baleen-image:1)\n"
    )
    pr = _make_parse_result(md_with_images)
    client = FakeClient([_VIDEO_SUCCESS_JSON])

    result = organize_note(pr, filename="v.md", ext=".md", client=client, video_images=video_images)
    assert result.organizable and result.blocks

    from app.service.notes_edit import _iter_images, doc_has_image_blocks

    assert doc_has_image_blocks(result.blocks)  # 图没丢
    imgs = _iter_images(result.blocks["content"])
    assert len(imgs) == 2
    assert imgs[0]["attrs"]["media"] == video_images[0]["media_key"]
    assert imgs[1]["attrs"]["media"] == video_images[1]["media_key"]


def test_organize_video_prompt_forbids_dropping_markers() -> None:
    """视频 prompt 必须明确禁止删除/移动图片标记（防模型汇总到文末）。"""
    from app.service.rewrite.organizer import _video_rules_text

    rules = _video_rules_text()
    assert "绝对不要删除标记" in rules
    assert "baleen-image:N" in rules
    assert "图跟着所属主题走" in rules


_VIDEO_DUPE_JSON = (
    '{"organizable": true, "markdown": "# 双例\\n\\n## 第一节\\n\\n'
    "一节内容。\\n\\n![[0:40] 一线城市家庭案例计算](baleen-image:0)\\n\\n"
    "## 第二节\\n\\n"
    "二节内容,想把同一张图再配一次。\\n\\n![[0:41] 反向推演600万案例](baleen-image:0)\\n\\n"
    '另一帧正常保留。\\n\\n![[1:46] 白板](baleen-image:1)", '
    '"quality": "core", "note_type": "concept", "reasons": ["融合语音与画面"]}'
)


def test_organize_video_dedupes_duplicate_frame_marker(tmp_path, monkeypatch) -> None:
    """organizer 复制帧标记 → 同 media key 的 image 块只留第一个（一帧一嵌）。

    2026-11-19 晚主人复核 6096 实锤：4.1/4.2 同帧同 media key 各嵌一次
    （LLM 想两节都配图时复制了 baleen-image:N，prompt 只禁删/挪没禁复制）。
    prompt 加了「编号最多出现一次」规则（软约束），本守卫是硬兜底。
    """
    from app.service import media as media_module
    from app.service.notes_edit import _iter_images
    from app.service.rewrite.organizer import organize_note

    monkeypatch.setattr(media_module, "MEDIA_ROOT", tmp_path / "media")
    video_images = _make_video_images(tmp_path)

    pr = _make_parse_result(_VIDEO_MD)
    client = FakeClient([_VIDEO_DUPE_JSON])
    result = organize_note(pr, filename="v.md", ext=".md", client=client, video_images=video_images)
    assert result.organizable and result.blocks

    imgs = _iter_images(result.blocks["content"])
    medias = [i["attrs"]["media"] for i in imgs]
    assert len(medias) == len(set(medias)), f"同帧重复嵌入: {medias}"
    assert medias.count(video_images[0]["media_key"]) == 1  # 复制的只留第一个
    assert medias.count(video_images[1]["media_key"]) == 1  # 正常帧不受影响


# ==================== 结构图依据闸（主人 2026-11-19 拍板：有依据才生） ====================

_LISTY_MD = (
    "# AI 入门地图\n\n"
    "本笔记按学习阶段分组介绍 AI 核心概念。\n\n"
    "## 模型层\n\n大模型的基本概念与原理。\n\n"
    "## 工具层\n\n各类 AI 工具产品。\n\n"
    "## 场景层\n\n视频生成与编程等场景。\n\n"
    "## 概念层\n\n从模型到 Skill 的概念梳理。\n\n"
    "## 执行层\n\n让 AI 真正做事的工程实践。\n"
)

# 节点全部来自原文标题 → 应保留
_LISTY_GOOD_JSON = (
    '{"organizable": true, "markdown": "# AI 入门地图\\n\\n本笔记按学习阶段分组。\\n'
    "\\n下图把 5 个层次归为一张地图。\\n\\n```mermaid\\nmindmap\\n  root((AI 入门))\\n"
    "    模型层\\n    工具层\\n    场景层\\n    概念层\\n    执行层\\n```\\n"
    "\\n从上到下是学习的推荐顺序。\\n\\n## 模型层\\n\\n大模型的基本概念与原理。\\n\\n## 工具层\\n"
    "\\n各类 AI 工具产品。\\n\\n## 场景层\\n\\n视频生成与编程等场景。\\n\\n## 概念层\\n"
    "\\n从模型到 Skill 的概念梳理。\\n\\n## 执行层\\n"
    '\\n让 AI 真正做事的工程实践。", "quality": "normal", "note_type":'
    ' "concept", "reasons": ["整理了清单"]}'
)

# 节点是模型自己编的概念 → 必须被摘掉
_LISTY_FAKE_JSON = (
    '{"organizable": true, "markdown": "# AI 入门地图\\n\\n本笔记按学习阶段分组。\\n'
    "\\n```mermaid\\nmindmap\\n  root((AI 全景))\\n    量子计算\\n"
    "    脑机接口\\n    可控核聚变\\n    太空电梯\\n```\\n"
    "\\n## 模型层\\n\\n大模型的基本概念与原理。\\n"
    "\\n## 工具层\\n\\n各类 AI 工具产品。\\n\\n## 场景层\\n\\n视频生成与编程等场景。\\n"
    "\\n## 概念层\\n\\n从模型到 Skill 的概念梳理。\\n\\n## 执行层\\n"
    '\\n让 AI 真正做事的工程实践。", "quality": "normal", '
    '"note_type": "concept", "reasons": ["整理了清单"]}'
)


def _make_listy_parse_result() -> ParseResult:
    """清单型笔记的 ParseResult——必须有真实 heading 块。

    ⚠️ 不能复用 _make_parse_result：它把整段文本塞进一个 paragraph，
    没有 heading，结构依据闸当然判定不出「清单」。生产路径里 G0 会把 md
    解析成带 heading 的 blocks，这里要模拟那一步。
    """

    def h(text: str, level: int) -> dict:
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": [{"type": "text", "text": text}],
        }

    def para(text: str) -> dict:
        return {"type": "paragraph", "content": [{"type": "text", "text": text}]}

    blocks = {
        "type": "doc",
        "content": [
            h("AI 入门地图", 1),
            para("本笔记按学习阶段分组介绍 AI 核心概念。"),
            h("模型层", 2),
            para("大模型的基本概念与原理。"),
            h("工具层", 2),
            para("各类 AI 工具产品。"),
            h("场景层", 2),
            para("视频生成与编程等场景。"),
            h("概念层", 2),
            para("从模型到 Skill 的概念梳理。"),
            h("执行层", 2),
            para("让 AI 真正做事的工程实践。"),
        ],
    }
    return ParseResult(parse_status=ParseStatus.OK, blocks=blocks, meta={"chars": 200})


def _has_mermaid_block(blocks: list) -> bool:
    def walk(items: list) -> bool:
        for b in items or []:
            if b.get("type") == "codeBlock":
                if str((b.get("attrs") or {}).get("language") or "").lower() == "mermaid":
                    return True
            if isinstance(b.get("content"), list) and walk(b["content"]):
                return True
        return False

    return walk(blocks)


def test_diagram_kept_when_nodes_traceable_to_source() -> None:
    """有结构依据 + 图里的节点全部来自原文 → 图保留，meta 记 basis 依据。"""
    from app.service.rewrite.organizer import organize_note

    pr = _make_listy_parse_result()
    result = organize_note(
        pr,
        filename="ai.md",
        ext=".md",
        client=FakeClient([_LISTY_GOOD_JSON]),
        allow_diagram=True,
    )
    assert result.organizable and result.blocks
    assert _has_mermaid_block(result.blocks["content"]), "有依据的好图不该被删"
    diagram = result.meta.get("diagram") or {}
    assert diagram.get("basis_kind") == "list"
    assert diagram.get("diagrams_kept") == 1


def test_diagram_dropped_when_nodes_fabricated() -> None:
    """图里节点是模型编的（量子计算/脑机接口不在原文）→ 整块摘掉并在 reasons 留痕。

    主人拍板原话:「怕 AI 乱生图、生无意义的图」——编造的图比没有图更糟。
    """
    from app.service.rewrite.organizer import organize_note

    pr = _make_listy_parse_result()
    result = organize_note(
        pr,
        filename="ai.md",
        ext=".md",
        client=FakeClient([_LISTY_FAKE_JSON]),
        allow_diagram=True,
    )
    assert result.organizable and result.blocks
    assert not _has_mermaid_block(result.blocks["content"]), "编造的图必须被摘掉"
    diagram = result.meta.get("diagram") or {}
    assert diagram.get("diagrams_dropped") == 1
    assert any("结构图已丢弃" in r for r in result.reasons)


def test_diagram_not_requested_when_no_basis() -> None:
    """没有结构依据（普通单主题笔记）→ prompt 里根本不出现画图任务。"""
    from app.service.rewrite.organizer import organize_note

    pr = _make_parse_result("这是一段关于闭包的技术内容,讲定义和用途。")
    client = FakeClient([_SUCCESS_JSON])
    result = organize_note(pr, filename="c.md", ext=".md", client=client, allow_diagram=True)
    assert result.organizable
    system_msg = client.received_messages[0][0]["content"]
    assert "结构图任务" not in system_msg, "没有依据就不该给 LLM 画图任务"
    assert not (result.meta.get("diagram") or {})


def test_diagram_disabled_by_default() -> None:
    """不传 allow_diagram（默认 False）→ 完全走老行为，不提画图。"""
    from app.service.rewrite.organizer import organize_note

    pr = _make_parse_result(_LISTY_MD)
    client = FakeClient([_LISTY_GOOD_JSON])
    result = organize_note(pr, filename="ai.md", ext=".md", client=client)
    assert result.organizable
    system_msg = client.received_messages[0][0]["content"]
    assert "结构图任务" not in system_msg
    assert not (result.meta.get("diagram") or {})
