"""结构图「依据闸」测试（主人 2026-11-19 拍板：全局默认开，但必须有依据才生）。

测的是**判定逻辑**，不是渲染：该画的要判定为该画，不该画的必须判定为不画，
编造的图必须被校验拦下。主人的原话是「怕 AI 乱生图、生无意义的图」——
所以每条用例都在问「这条闸能不能挡住一次乱画」。
"""

from __future__ import annotations

from app.service.rewrite.diagram_basis import (
    DiagramBasis,
    build_diagram_prompt,
    detect_diagram_basis,
    extract_mermaid_blocks,
    validate_diagram,
)


def _heading(text: str, level: int = 2) -> dict:
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*blocks: dict) -> list:
    return list(blocks)


# ---------------------------------------------------------------- 清单型判定


def test_list_basis_hits_on_many_short_sibling_headings() -> None:
    """同级标题 ≥5 个且每节都短 → 判定为清单,节点来自标题。"""
    blocks = _doc(
        _heading("AI 入门地图", 1),
        _heading("模型层", 2),
        _para("讲大模型的基本概念和原理。"),
        _heading("工具层", 2),
        _para("介绍各类 AI 工具产品。"),
        _heading("场景层", 2),
        _para("覆盖视频生成与编程等场景。"),
        _heading("概念层", 2),
        _para("从模型到 Skill 的概念梳理。"),
        _heading("执行层", 2),
        _para("让 AI 真正做事的工程实践。"),
    )
    basis = detect_diagram_basis(blocks)
    assert basis.kind == "list"
    assert "模型层" in basis.nodes and "执行层" in basis.nodes
    assert "5 个同级标题" in basis.evidence


def test_list_basis_rejects_deep_content() -> None:
    """同样是 5 个同级标题,但每节都很长(深入讲解)→ 不是清单,不许压成图。"""
    long_text = "这是一个非常详细的讲解段落。" * 60  # > 600 字
    blocks = _doc(
        _heading("深度解析", 1),
        _heading("第一原理", 2), _para(long_text),
        _heading("第二原理", 2), _para(long_text),
        _heading("第三原理", 2), _para(long_text),
        _heading("第四原理", 2), _para(long_text),
        _heading("第五原理", 2), _para(long_text),
    )
    basis = detect_diagram_basis(blocks)
    assert basis.kind == "", "长文笔记不是清单,不该被压成结构图"


def test_list_basis_needs_five_items() -> None:
    """只有 4 个同级标题 → 不触发(图的收益抵不过噪音风险)。"""
    blocks = _doc(
        _heading("总览", 1),
        _heading("甲", 2), _para("内容。"),
        _heading("乙", 2), _para("内容。"),
        _heading("丙", 2), _para("内容。"),
        _heading("丁", 2), _para("内容。"),
    )
    assert detect_diagram_basis(blocks).kind == ""


# ---------------------------------------------------------------- 流程型判定


def test_flow_basis_hits_on_sequence_markers() -> None:
    """正文有 3 种以上顺序表述 + 标题含阶段名 → 判定为流程。"""
    blocks = _doc(
        _heading("Docker 命令流程", 1),
        _para("首先拉取镜像,然后运行容器,最后推送镜像。"),
        _heading("拉取镜像", 2),
        _heading("运行容器", 2),
        _heading("推送镜像", 2),
    )
    basis = detect_diagram_basis(blocks)
    assert basis.kind == "flow"
    assert len(basis.nodes) >= 3


def test_flow_basis_needs_three_markers() -> None:
    """只有「先/然后」两个顺序词 → 不触发。"""
    blocks = _doc(
        _heading("两个阶段", 1),
        _para("先做这个,然后做那个。"),
        _heading("阶段甲", 2),
        _heading("阶段乙", 2),
        _heading("阶段丙", 2),
    )
    assert detect_diagram_basis(blocks).kind == ""


def test_plain_note_gets_no_basis() -> None:
    """普通单主题笔记(PPO 那种)不该被判为需要结构图。"""
    blocks = _doc(
        _heading("PPO 基本原理", 1),
        _para("PPO 是一种给策略更新加限速的强化学习算法。"),
        _heading("裁剪目标函数", 2),
        _para("通过裁剪比值限制每次更新的幅度,避免策略崩坏。"),
        _heading("优势函数", 2),
        _para("用优势函数代替累计回报,降低方差。"),
    )
    basis = detect_diagram_basis(blocks)
    assert basis.kind == ""
    assert basis.reason_skip


# ---------------------------------------------------------------- 产出校验


def test_validate_accepts_diagram_using_source_nodes() -> None:
    """图里节点全部来自原文标题 → 通过。"""
    basis = DiagramBasis(kind="list", nodes=["模型层", "工具层", "场景层", "概念层", "执行层"])
    code = (
        "mindmap\n  root((AI 入门地图))\n    模型层\n    工具层\n"
        "    场景层\n    概念层\n    执行层"
    )
    ok, why = validate_diagram(code, basis)
    assert ok, why


def test_validate_rejects_fabricated_nodes() -> None:
    """图里出现原文没有的概念(模型自己编的)→ 必须拦下。"""
    basis = DiagramBasis(kind="list", nodes=["模型层", "工具层", "场景层"])
    code = (
        "mindmap\n  root((AI 全景))\n    量子计算\n    脑机接口\n"
        "    可控核聚变\n    太空电梯"
    )
    ok, why = validate_diagram(code, basis)
    assert not ok
    assert "编造" in why


def test_validate_rejects_when_no_basis() -> None:
    """没有结构依据却生成了图 → 直接拒绝(这是「乱生图」的主路径)。"""
    ok, why = validate_diagram("mindmap\n  root((x))\n    a", DiagramBasis(kind=""))
    assert not ok
    assert "没有结构依据" in why


def test_validate_rejects_dumping_whole_text() -> None:
    """节点数超过 45 → 这是在抄全文不是画结构。"""
    basis = DiagramBasis(kind="list", nodes=[f"节点{i}" for i in range(60)])
    code = "graph TD\n" + "\n".join(f'  n{i}["节点{i}"]' for i in range(60))
    ok, why = validate_diagram(code, basis)
    assert not ok
    assert "超过 45" in why


def test_validate_tolerates_numbering_prefix() -> None:
    """LLM 常把「2.1 模型层」写成「模型层」——允许编号前缀差异。"""
    basis = DiagramBasis(kind="list", nodes=["2.1 模型层", "2.2 工具层", "2.3 场景层"])
    code = "mindmap\n  root((AI))\n    模型层\n    工具层\n    场景层"
    ok, why = validate_diagram(code, basis)
    assert ok, why


# ---------------------------------------------------------------- prompt 组装


def test_build_prompt_lists_source_nodes_only() -> None:
    """注入 prompt 时必须带上「必须用到的节点」清单(不许自由发挥)。"""
    basis = DiagramBasis(kind="list", nodes=["甲", "乙", "丙"], evidence="3 个同级标题")
    prompt = build_diagram_prompt(basis)
    assert "甲" in prompt and "乙" in prompt and "丙" in prompt
    assert "并列清单" in prompt
    assert "整篇只画这一张" in prompt


def test_extract_mermaid_blocks() -> None:
    md = "文字\n```mermaid\nflowchart TD\n  A --> B\n```\n更多文字\n```mermaid\nmindmap\n  root\n```"
    blocks = extract_mermaid_blocks(md)
    assert len(blocks) == 2
    assert "flowchart" in blocks[0]
    assert "mindmap" in blocks[1]
