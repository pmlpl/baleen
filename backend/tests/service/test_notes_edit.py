"""笔记自编辑纯函数测试:doc→markdown 序列化 + 图片块检测。"""

from __future__ import annotations

from app.service.notes_edit import doc_has_image_blocks, doc_to_markdown
from app.service.parse.md_parser import parse_md_text

SAMPLE_MD = r"""# 主标题

普通段落,带 **加粗**、*斜体*、`代码`、[链接](https://example.com) 和 ~~删除线~~。

## 二级节

- 无序列表甲
- 无序列表乙

1. 有序第一
2. 有序第二

```python
def hello() -> str:
    return "hi"
```

| 名称 | 说明 |
| --- | --- |
| 甲 | 包含\|竖线的单元格 |
| 乙 | 第二行 |

> 引用一行

---

结尾段落。
"""


def test_round_trip_is_fixed_point():
    """md → doc → md 往返是定点:第二次序列化 == 第一次(再解析不再变形)。"""
    doc1, _ = parse_md_text(SAMPLE_MD)
    md1 = doc_to_markdown({"type": "doc", "content": doc1})
    doc2, _ = parse_md_text(md1)
    md2 = doc_to_markdown({"type": "doc", "content": doc2})
    assert md1 == md2, f"往返不稳定:\n---第一次---\n{md1}\n---第二次---\n{md2}"


def test_round_trip_keeps_structure_and_marks():
    doc1, _ = parse_md_text(SAMPLE_MD)
    md1 = doc_to_markdown({"type": "doc", "content": doc1})
    # 结构关键件都在
    assert "# 主标题" in md1
    assert "## 二级节" in md1
    assert "**加粗**" in md1 and "*斜体*" in md1 and "`代码`" in md1
    assert "[链接](https://example.com)" in md1 and "~~删除线~~" in md1
    assert "```python" in md1
    assert "| 名称 | 说明 |" in md1 and "| --- | --- |" in md1
    assert "\\|" in md1, "单元格里的竖线要转义"
    assert "> 引用一行" in md1
    assert "\n---\n" in md1
    # 类型不丢:块类型集合一致(文本节点数量一致)
    types1 = sorted(b["type"] for b in doc1)
    doc2, _ = parse_md_text(md1)
    types2 = sorted(b["type"] for b in doc2)
    assert types1 == types2


def test_paragraph_leading_metachar_escaped():
    """行首 # 会被转义,普通段落不会往返成标题。"""
    doc1, _ = parse_md_text("普通段落")
    doc1[0]["content"][0]["text"] = "# 看起来像标题的段落"
    md = doc_to_markdown({"type": "doc", "content": doc1})
    doc2, _ = parse_md_text(md)
    para = doc2[0]
    assert para["type"] == "paragraph", "行首 # 必须被转义,不能往返成标题"
    assert para["content"][0]["text"] == "# 看起来像标题的段落"


def test_nested_list_two_levels():
    doc = {
        "type": "doc",
        "content": [
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
        ],
    }
    md = doc_to_markdown(doc)
    assert "- 父项" in md
    assert "  - 子项" in md, "子项要有两格缩进"
    doc2, _ = parse_md_text(md)
    assert doc2[0]["type"] == "bulletList"


def test_code_block_with_fence_inside():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "codeBlock",
                "attrs": {"language": "md"},
                "content": [{"type": "text", "text": "```js\nconsole.log(1)\n```"}],
            }
        ],
    }
    md = doc_to_markdown(doc)
    assert md.count("```") % 2 == 0, "围栏必须成对(内部 ``` 触发加长)"
    assert md.startswith("````md"), "内部有 ``` 时外层围栏加长"
    doc2, _ = parse_md_text(md)
    code = doc2[0]
    assert code["type"] == "codeBlock"
    assert "".join(n["text"] for n in code["content"]) == "```js\nconsole.log(1)\n```"


def test_empty_and_none_docs():
    assert doc_to_markdown(None) == ""
    assert doc_to_markdown({"type": "doc", "content": []}) == ""


def test_image_detection():
    assert doc_has_image_blocks({"type": "doc", "content": [{"type": "image", "attrs": {}}]})
    assert not doc_has_image_blocks({"type": "doc", "content": [{"type": "paragraph"}]})
    assert not doc_has_image_blocks(None)
    nested = {
        "type": "doc",
        "content": [
            {
                "type": "blockquote",
                "content": [{"type": "paragraph", "content": [{"type": "image", "attrs": {}}]}],
            }
        ],
    }
    assert doc_has_image_blocks(nested), "嵌套在引用里的图也要查到"
