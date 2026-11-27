"""doc→md→doc 往返恒等测试(2026-11-22 追加批)。

三个往返失真坑的回归钉:
1. 粗体 rule-of-3:``**A**正文**B**``(粗体紧邻正文再粗体,且粗体以标点收尾)
   旧解析堆叠粗体/整行塌纯文本(质检修正批 4982 实锤)——解析侧 scanDelims 松弛修;
2. 字面下划线:``__name__`` 旧解析变粗体(6090 实锤)——序列化侧 ``\\_`` 转义修;
3. 有序项嵌套子弹:旧序列化固定缩 2 格,CommonMark 需 ≥3 格才嵌套,往返拍平
   (4982/4983 实锤)——序列化侧按父项标记宽度缩进修。
"""

from __future__ import annotations

import pytest

from app.service.notes_edit import doc_to_markdown
from app.service.parse.md_parser import parse_md_text


def _doc(*blocks: dict) -> dict:
    return {"type": "doc", "content": list(blocks)}


def _para(*inlines: dict) -> dict:
    return {"type": "paragraph", "content": list(inlines)}


def _t(text: str, *marks: dict) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def _b(text: str) -> dict:
    return _t(text, {"type": "bold"})


def _runs(nodes: list[dict]) -> list[tuple[tuple[str, ...], str]]:
    """行内节点 → (marks 元组, 文本) 序列;空文本节点是解析器产物,剥掉再比。"""
    return [
        (tuple(m["type"] for m in ch.get("marks") or []), ch.get("text", ""))
        for ch in nodes
        if ch.get("type") == "text" and ch.get("text", "") != ""
    ]


def _first_para(blocks: list[dict]) -> list[dict]:
    blk = blocks[0]
    if blk.get("type") in ("bulletList", "orderedList"):
        return blk["content"][0]["content"][0]["content"]
    return blk["content"]


def test_bold_trapped_close_keeps_boundary():
    """坑1a(4982 实锤形态):粗体以 $ 收尾+紧邻正文再粗体,往返后粗体边界不变。"""
    para = _para(
        _b("组合得到电荷分布 $\\rho$"),
        _t("将上述结果代回泊松方程并整理，这表明屏蔽势对应一个"),
        _b("连续体电荷分布"),
        _t("（指数衰减项）加上原点的一个奇异项。"),
    )
    doc = _doc(para)
    md = doc_to_markdown(doc)
    assert md == (
        "**组合得到电荷分布 $\\rho$**将上述结果代回泊松方程并整理，"
        "这表明屏蔽势对应一个**连续体电荷分布**（指数衰减项）加上原点的一个奇异项。"
    ), f"序列化形态变了: {md}"
    doc2, _ = parse_md_text(md)
    assert _runs(_first_para(doc2)) == _runs(para["content"]), "粗体边界往返变形"


@pytest.mark.xfail(
    reason="已知限制:粗体以标点开头+紧邻正文,spec 打不开 → 往返丢粗体;"
    "打开侧松弛会偷走合法闭合符(见 md_parser 往返友好松弛节注释),"
    "需 emphasis 规则层查 pending opener 才能根治"
)
def test_bold_trapped_open_keeps_boundary():
    """坑1b:正文紧邻以标点开头的粗体(``中文**（说明）**正文``),往返后边界不变。"""
    para = _para(_t("中文"), _b("（说明）"), _t("正文继续。"))
    md = doc_to_markdown(_doc(para))
    assert md == "中文**（说明）**正文继续。", f"序列化形态变了: {md}"
    doc2, _ = parse_md_text(md)
    assert _runs(_first_para(doc2)) == _runs(para["content"])


def test_bold_ascii_trapped_close():
    """坑1c(ASCII):粗体以句点收尾+紧邻字母,spec 下闭不上,松弛后往返恒等。"""
    para = _para(_b("foo."), _t("bar 继续文字"))
    md = doc_to_markdown(_doc(para))
    assert md == "**foo.**bar 继续文字"
    doc2, _ = parse_md_text(md)
    assert _runs(_first_para(doc2)) == _runs(para["content"])


def test_bold_letter_boundary_unchanged():
    """无标点的普通粗体不受松弛影响(``**foo**bar`` 照常)。"""
    para = _para(_b("foo"), _t("bar"))
    doc2, _ = parse_md_text(doc_to_markdown(_doc(para)))
    assert _runs(_first_para(doc2)) == _runs(para["content"])


def test_literal_underscore_heading_not_bolded():
    """坑2(6090 实锤):标题里的字面 ``__name__`` 往返必须保持纯文本。"""
    heading = {
        "type": "heading",
        "attrs": {"level": 2},
        "content": [_t('1.1 用 if __name__ == "__main__" 包裹测试与主逻辑')],
    }
    md = doc_to_markdown(_doc(heading))
    assert "\\_\\_name\\_\\_" in md, "下划线必须转义,否则往返成粗体"
    doc2, _ = parse_md_text(md)
    blk = doc2[0]
    assert blk["type"] == "heading"
    runs = _runs(blk["content"])
    assert runs == [((), '1.1 用 if __name__ == "__main__" 包裹测试与主逻辑')], (
        f"标题往返变形: {runs}"
    )


def test_literal_underscore_and_asterisk_body():
    """正文里的 snake_case/字面星号/字面删除线往返后文本与 marks 都不变。"""
    para = _para(_t("配置 my_config_value 与 **字面星号** 和 ~~字面线~~ 原样保留。"))
    md = doc_to_markdown(_doc(para))
    doc2, _ = parse_md_text(md)
    assert _runs(_first_para(doc2)) == _runs(para["content"])


def test_real_bold_still_renders_bold():
    """转义不误伤真粗体(纯中文粗体文本往返仍是 bold mark)。"""
    para = _para(_t("前置"), _b("加粗内容"), _t("后置"))
    doc2, _ = parse_md_text(doc_to_markdown(_doc(para)))
    assert _runs(_first_para(doc2)) == _runs(para["content"])


def _nested_ordered_doc(start: int) -> dict:
    def bullet(text: str) -> dict:
        return {
            "type": "listItem",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        }

    return _doc(
        {
            "type": "orderedList",
            "attrs": {"start": start},
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "坐标推导"}]},
                        {"type": "bulletList", "content": [bullet("子弹甲"), bullet("子弹乙")]},
                    ],
                },
                {
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "下一项"}]}
                    ],
                },
            ],
        }
    )


def test_ordered_nested_bullet_not_flattened():
    """坑3(4982/4983 实锤):有序项下的嵌套子弹往返后仍是嵌套(3 格缩进)。"""
    doc = _nested_ordered_doc(start=4)
    md = doc_to_markdown(doc)
    assert "\n   - 子弹甲" in md, f"有序项下嵌套应缩 3 格: {md!r}"
    doc2, _ = parse_md_text(md)
    ol = doc2[0]
    assert ol["type"] == "orderedList"
    nested = [c.get("type") for c in ol["content"][0]["content"]]
    assert "bulletList" in nested, f"嵌套子弹被拍平: {nested}"


def test_two_digit_ordered_nested_bullet():
    """两位数编号 ``10. `` 标记宽 4 格,嵌套缩进跟着变(潜伏 bug 顺带钉住)。"""
    doc = _nested_ordered_doc(start=10)
    md = doc_to_markdown(doc)
    assert "\n    - 子弹甲" in md, f"两位数编号下嵌套应缩 4 格: {md!r}"
    doc2, _ = parse_md_text(md)
    nested = [c.get("type") for c in doc2[0]["content"][0]["content"]]
    assert "bulletList" in nested


def test_bullet_nested_bullet_still_two_spaces():
    """子弹父项的嵌套仍是 2 格(旧行为不回归)。"""
    doc = _doc(
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "父项"}]},
                        {
                            "type": "bulletList",
                            "content": [
                                {
                                    "type": "listItem",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "子项"}],
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    )
    md = doc_to_markdown(doc)
    assert "\n  - 子项" in md


def test_combined_doc_roundtrip_identity():
    """三坑合体:doc→md→doc 的结构投影与原 doc 完全一致,且 md 二次序列化定点。"""
    para_a = _para(
        _b("组合得到电荷分布 $\\rho$"),
        _t("将上述结果代回泊松方程并整理，这表明屏蔽势对应一个"),
        _b("连续体电荷分布"),
        _t("（指数衰减项）。"),
    )
    heading = {
        "type": "heading",
        "attrs": {"level": 2},
        "content": [_t('1.1 用 if __name__ == "__main__" 包裹')],
    }
    doc = _doc(para_a, heading, _nested_ordered_doc(start=4)["content"][0])
    md1 = doc_to_markdown(doc)
    doc2, _ = parse_md_text(md1)
    md2 = doc_to_markdown({"type": "doc", "content": doc2})
    assert md1 == md2, f"往返不是定点:\n---md1---\n{md1}\n---md2---\n{md2}"

    def project(blocks: list[dict]) -> list:
        out = []
        for blk in blocks:
            t = blk.get("type")
            if t == "heading":
                out.append(("heading", blk["attrs"]["level"], _runs(blk["content"])))
            elif t == "paragraph":
                out.append(("paragraph", _runs(blk["content"])))
            elif t in ("bulletList", "orderedList"):
                out.append((t, [(project(i["content"])) for i in blk["content"]]))
        return out

    assert project(doc2) == project(doc["content"]), "三坑合体往返投影不一致"
