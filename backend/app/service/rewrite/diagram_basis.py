"""生成式结构图的「依据闸」——先算依据，才允许画图（2026-11-19 主人拍板）。

## 为什么要有这个模块

主人的顾虑：**「怕 AI 乱生图、生无意义的图」**。所以不能写成 prompt 里一句
「需要时画个图」——LLM 会觉得处处都需要。这里把「该不该画」做成**代码判定**：

1. **依据先算出来**（本模块 `detect_diagram_basis`）：只认可两种可计数的结构形态
   - 并列清单：同级标题 ≥ 5 个，且这些小节都不长（每节 < 600 字）
   - 流程阶段：明确的顺序表述 ≥ 3 处（先…再…最后 / 第一步…第二步…）
2. **依据注入 prompt**：命中了才把「依据 + 必须用到的节点清单」交给 LLM；
   没命中就完全不提画图这件事——不给 LLM 自由发挥的空间
3. **产出后校验**（`validate_diagram`）：图里的节点文字必须**全部出现在原文**里，
   否则这张图是模型编的概念，直接丢掉（保留代码块退化，不会说谎）

## 为什么不判「概念演进层级」

提案里原本列了 T3（A 由 B 组成，B 又分为 C）。实测无法可靠用代码判定：
中文的「由…组成」「分为」既用于真层级也用于修辞，误判率高于收益。
先只做 T1/T2 两种**可计数**的形态——宁可少画，不可乱画（主人的原话）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---- 阈值（全部可计数,不靠感觉）----
MIN_LIST_ITEMS = 5  # 并列清单至少 5 项
MAX_ITEM_CHARS = 600  # 且每项都短(长文是"深入讲解"不是"清单",不该压成图)
MIN_FLOW_STEPS = 3  # 流程至少 3 阶段


@dataclass
class DiagramBasis:
    """一次结构图判定的依据。basis 为空 = 不画。"""

    kind: str  # "list" | "flow" | ""
    nodes: list[str] = field(default_factory=list)  # 必须出现的节点文字(来自原文)
    evidence: str = ""  # 人话依据(写进日志/prompt)
    reason_skip: str = ""  # 不画的原因(仅调试用)


def _walk_blocks(blocks: list) -> list[dict]:
    """展开 blocks 树(标题/段落可能在嵌套结构里)。"""
    out: list[dict] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        out.append(b)
        if isinstance(b.get("content"), list):
            out.extend(_walk_blocks(b["content"]))
    return out


def _text_of(block: dict) -> str:
    parts: list[str] = []
    for c in block.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
    return "".join(parts)


def _heading_level(block: dict) -> int:
    try:
        return int((block.get("attrs") or {}).get("level") or 0)
    except (TypeError, ValueError):
        return 0


# 顺序词:命中 ≥ MIN_FLOW_STEPS 个不同的才认流程
_FLOW_MARKERS = (
    "第一步", "第二步", "第三步", "第四步", "最后一步",
    "首先", "其次", "然后", "接着", "最后",
    "先", "再", "之后", "随后",
)


def _detect_flow(blocks: list[dict], full_text: str) -> DiagramBasis | None:
    """流程判定:数「顺序词」的出现种类(不是次数,避免同一词刷屏)。"""
    hits = [m for m in _FLOW_MARKERS if m in full_text]
    # 「先…再…」这种两词也是流程,但只有 2 个词时信息不足以画图
    if len(hits) < MIN_FLOW_STEPS:
        return None
    # 节点从标题取(标题是作者已经提炼过的阶段名,比从正文猜稳)
    headings = [_text_of(b).strip() for b in blocks if b.get("type") == "heading"]
    nodes = [h for h in headings if h and len(h) <= 20]
    if len(nodes) < MIN_FLOW_STEPS:
        return None
    return DiagramBasis(
        kind="flow",
        nodes=nodes[:8],
        evidence=f"正文有 {len(hits)} 种顺序表述({'/'.join(hits[:5])}),标题含 {len(nodes)} 个阶段名",
    )


def _detect_list(blocks: list[dict]) -> DiagramBasis | None:
    """并列清单判定:同级标题 ≥5 个,且每个标题到下一个同级标题之间都很短。"""
    # 按标题分组:记每个标题的层级 + 它下面(到下一个同级或更高级标题前)的字数
    sections: list[dict] = []
    current: dict | None = None
    for b in blocks:
        if b.get("type") == "heading":
            level = _heading_level(b)
            title = _text_of(b).strip()
            if not title:
                continue
            current = {"level": level, "title": title, "chars": 0}
            sections.append(current)
            continue
        if current is not None:
            current["chars"] += len(_text_of(b))

    # 找「同级 ≥ MIN_LIST_ITEMS 个,且都短」的那一组
    from collections import defaultdict

    by_level: dict[int, list[dict]] = defaultdict(list)
    for s in sections:
        if s["level"] >= 2:  # 一级标题是章,清单项一般在二级及以下
            by_level[s["level"]].append(s)

    best: list[dict] | None = None
    for level, group in by_level.items():
        short = [s for s in group if s["chars"] <= MAX_ITEM_CHARS]
        if len(short) >= MIN_LIST_ITEMS and (best is None or len(short) > len(best)):
            best = short
    if best is None:
        return None

    nodes = [s["title"] for s in best]
    return DiagramBasis(
        kind="list",
        nodes=nodes[:20],  # 节点太多图会糊,最多 20 个
        evidence=(
            f"{len(best)} 个同级标题(H{best[0]['level']}),"
            f"每个小节 ≤ {MAX_ITEM_CHARS} 字(是清单不是深入讲解)"
        ),
    )


def detect_diagram_basis(blocks: list) -> DiagramBasis:
    """算「这篇笔记该不该配结构图」。返回 basis.kind=="" 就是不该画。

    判定顺序:清单优先(信息量更大),再流程。
    """
    flat = _walk_blocks(blocks)
    full_text = "".join(
        _text_of(b) for b in flat if b.get("type") in ("paragraph", "heading")
    )

    list_basis = _detect_list(flat)
    if list_basis is not None:
        return list_basis

    flow_basis = _detect_flow(flat, full_text)
    if flow_basis is not None:
        return flow_basis

    return DiagramBasis(kind="", reason_skip="没有可计数的并列清单或流程结构")


def extract_mermaid_blocks(markdown: str) -> list[str]:
    """取出 markdown 里的 mermaid 代码块(用于产出后校验)。"""
    return re.findall(r"```mermaid\s*\n(.*?)```", markdown, re.S)


def _extract_diagram_labels(code: str) -> list[str]:
    """从 mermaid 源码里抽「节点文字」。

    两种语法形态都要覆盖，否则会漏掉一半节点：
    - flowchart/graph: ``A[文字]`` / ``A(文字)`` / ``A{文字}`` / ``A["文字"]``
    - mindmap: 裸缩进行（``    模型层``），没有括号
    抽取后去掉编号前缀/箭头/引号，去重保序。
    """
    skip_prefixes = (
        "graph", "flowchart", "mindmap", "subgraph", "end", "style", "classDef",
        "class ", "direction", "%%", "click ", "linkStyle",
    )
    arrow = re.compile(r"-->|--->|==>|-\.->|---|->>|-->>|~~~")

    labels: list[str] = []
    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(skip_prefixes):
            continue
        # 1) 括号内文字（可含嵌套括号: root((文字)) ）
        found = re.findall(r"[\[\(\{]([^\[\]\(\)\{\}]+)[\]\)\}]", line)
        if found:
            labels.extend(f.strip().strip("\"'") for f in found)
        # 2) 裸文本行（mindmap）或箭头两侧的裸标识符
        for token in arrow.split(line):
            token = token.strip().strip("\"'")
            # 短纯 ASCII 标识符是 mermaid 的节点 id（A[..] 里的 A），不当作文字
            if re.fullmatch(r"[A-Za-z0-9_]+", token) and len(token) <= 3:
                continue
            if token:
                labels.append(token)

    # 去重保序 + 去掉纯符号
    seen: set[str] = set()
    out: list[str] = []
    for label in labels:
        label = label.strip().strip("\"'").strip()
        if not label or label in seen:
            continue
        if re.fullmatch(r"[\W_]+", label):
            continue
        seen.add(label)
        out.append(label)
    return out


def validate_diagram(code: str, basis: DiagramBasis) -> tuple[bool, str]:
    """校验一张 mermaid 图是否「有依据」。

    规则(宁可判错丢弃,不可放行乱画):
    1. 图里的节点文字必须**至少一半**出现在原文节点清单里
       (LLM 允许归组/加分组名,但叶子必须来自原文)
    2. 图不能超过 45 个节点(超过多半是抄全文,不是结构图)

    返回 (是否通过, 原因)。
    """
    if basis.kind == "":
        return False, "没有结构依据却生成了图"

    labels = _extract_diagram_labels(code)
    if not labels:
        return False, "图里没有任何节点文字"

    if len(labels) > 45:
        return False, f"图有 {len(labels)} 个节点,超过 45(这是在抄全文不是画结构)"

    # 依据节点 → 是否被原文节点覆盖(允许子串匹配:LLM 常把「2.1 XXX」写成「XXX」)
    basis_texts = [re.sub(r"^[\d.\s、)）]+", "", b.strip()) for b in basis.nodes if b.strip()]

    def covered(label: str) -> bool:
        clean = re.sub(r"^[\d.\s、)）]+", "", label)
        return any(clean in b or b in clean for b in basis_texts)

    hit = sum(1 for label in labels if covered(label))
    ratio = hit / len(labels)
    if ratio < 0.5:
        return False, (
            f"只有 {hit}/{len(labels)} 个节点有原文依据(需 ≥50%),"
            "说明模型在编造结构"
        )
    return True, f"{hit}/{len(labels)} 个节点有原文依据"


def build_diagram_prompt(basis: DiagramBasis) -> str:
    """把依据写成 prompt 片段。只在 basis.kind 非空时调用。"""
    kind_word = "并列清单" if basis.kind == "list" else "流程阶段"
    node_lines = "\n".join(f"  - {n}" for n in basis.nodes)
    graph_kind = "mindmap 或 flowchart LR(按主题分组)" if basis.kind == "list" else "flowchart TD(按先后顺序)"
    return f"""

【结构图任务】（本篇**已确诊**为{kind_word}结构，必须画且只画 1 张）

依据：{basis.evidence}

**必须用到的节点**（这些文字来自原文，不许新增原文没有的概念）：
{node_lines}

要求：
- 用 ```mermaid 代码块输出 1 张 {graph_kind}；
- 节点文字**只能**用上面清单里的词，可以归组加分组名，但不许出现清单外的概念；
- 每个节点文字 ≤ 14 个汉字（超了会换行/裁切）；
- 图**之前**必须有一句引导（「下图把 … 归为 …」），图**之后**必须跟一句解读；
- 不要用 mermaid 的 style/classDef 自定义配色；
- **整篇只画这一张**，不要在别的小节再插图。
"""
