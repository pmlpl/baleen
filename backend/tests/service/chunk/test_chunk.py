"""切块器单测(ADR 0004)。

用假 token_counter(按非空白字符数)跑,不加载 bge 模型——验证结构与硬指标。
真实 token 计数在 e2e 测试里走 app.service.embed.count_tokens。
"""

from __future__ import annotations

import re

from app.service.chunk import chunk


# 假 token 计数器:1 非空白字符 ≈ 1 token(与 ADR §1 的"1 汉字≈1 token"一致)
def FAKE_TC(t: str) -> int:
    return len(re.sub(r"\s+", "", t))


def _text(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _heading(level: int, text: str) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _norm_chars(s: str) -> int:
    return len(re.sub(r"\s+", "", s))


def _assert_hard_metrics(r) -> None:
    assert r.metrics["chunks_over_limit"] == 0, "任何子块 token > 480"
    assert r.metrics["chunks_truncated"] == 0, "被 tokenizer 截断"


class TestChunkBasic:
    def test_short_note_single_section(self):
        doc = {"type": "doc", "content": [_heading(1, "短笔记"), _text("一句话笔记。")]}
        r = chunk(doc, token_counter=FAKE_TC)
        assert len(r.sections) == 1
        assert r.metrics["chunks_total"] >= 2  # 1 父 + 1 子
        _assert_hard_metrics(r)

    def test_long_text_multi_chunk(self):
        long_para = "闭包是函数与其词法环境的组合。" * 30  # ~900 字
        doc = {"type": "doc", "content": [_heading(1, "闭包"), _text(long_para)]}
        r = chunk(doc, token_counter=FAKE_TC)
        children = [c for c in r.chunks if not c.is_parent]
        assert len(children) >= 2, "长文应切成多个子块"
        for c in children:
            assert _norm_chars(c.text) <= 400 or len(children) == 1
        _assert_hard_metrics(r)


class TestTable:
    def test_big_table_row_by_row_with_header(self):
        header_row = {
            "type": "tableRow",
            "content": [
                {
                    "type": "tableHeader",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "名称"}]}
                    ],
                },
                {
                    "type": "tableHeader",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "描述"}]}
                    ],
                },
            ],
        }
        rows = [
            {
                "type": "tableRow",
                "content": [
                    {
                        "type": "tableCell",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": f"参数项{i}的完整名称"}],
                            }
                        ],
                    },
                    {
                        "type": "tableCell",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": f"这是第{i}行参数的详细描述说明文字"}
                                ],
                            }
                        ],
                    },
                ],
            }
            for i in range(30)
        ]
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "参数表"),
                {"type": "table", "content": [header_row] + rows},
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        table_chunks = [c for c in r.chunks if c.meta.get("block_type") == "table"]
        assert len(table_chunks) >= 2, "大表格应按行切成多块"
        for c in table_chunks:
            assert c.text.startswith("[表格("), "每块带分片标识"
            assert "表头：名称 | 描述" in c.text, "每块重复表头(ADR §4.2)"
        _assert_hard_metrics(r)


class TestCode:
    def test_long_code_by_function_boundary(self):
        code = "\n".join(f"def func_{i}():\n    x = {i}\n    return x + 1\n" for i in range(3))
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "工具函数"),
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": code}],
                },
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        code_chunks = [c for c in r.chunks if c.meta.get("block_type") == "code"]
        # 函数边界切过(_chunk_code 阶段按 def 分组);微型函数会被 §4.7 合并并成更少块
        assert len(code_chunks) <= 3
        for c in code_chunks:
            assert c.text.startswith("# "), "每块带 context 行(ADR §4.3)"
            assert "python" in c.text
        # 三个函数的内容都没丢(合并不丢语义)
        joined = "\n".join(c.text for c in code_chunks)
        for i in range(3):
            assert f"func_{i}" in joined
        _assert_hard_metrics(r)

    def test_over_limit_code_split_by_lines(self):
        # 单个函数超硬上限:按行再切成多片,每片 ≤ 480,不再整段静默截断(HANDOFF 真问题 5)
        lines = [f"    step_{i:02d} = 计算第{i:02d}步的结果值并写回缓冲区" for i in range(35)]
        code = "def big():" + "\n" + "\n".join(lines)
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "大函数"),
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": code}],
                },
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        code_chunks = [c for c in r.chunks if c.meta.get("block_type") == "code"]
        assert len(code_chunks) >= 2, "超限函数应按行切成多片"
        for c in code_chunks:
            assert c.token_count <= 480, f"切片仍超硬上限:{c.token_count}"
            assert c.text.startswith("# "), "每片重复 context 行(检索消歧)"
        assert sorted(c.meta["code_part"] for c in code_chunks) == list(
            range(1, len(code_chunks) + 1)
        ), "分片编号连续"
        joined = "\n".join(c.text for c in code_chunks)
        for i in range(35):
            assert f"step_{i:02d}" in joined, "切分不丢内容"
        _assert_hard_metrics(r)

    def test_single_monster_line_kept_with_warning(self):
        # 单行超预算:整行保留(行是代码最小语义单元,同正文「单句超长」口径),但必须显式告警
        code = "data = '" + "x" * 600 + "'"
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "坏样例"),
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": code}],
                },
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        code_chunks = [c for c in r.chunks if c.meta.get("block_type") == "code"]
        assert len(code_chunks) == 1, "单行不可再切,整行保留"
        assert code_chunks[0].token_count > 480
        assert any("整行保留未切" in w for w in r.metrics["warnings"]), "必须告警不许静默"
        assert r.metrics["chunks_truncated"] == 1

    def test_small_functions_unaffected(self):
        # 未超限的代码块行为与旧版一致:不加分片编号
        code = "\n".join(f"def func_{i}():\n    return {i}\n" for i in range(2))
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "小函数"),
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": code}],
                },
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        for c in r.chunks:
            if c.meta.get("block_type") == "code":
                assert "code_part" not in c.meta
        _assert_hard_metrics(r)


class TestTocAndImage:
    def test_toc_page_skipped_embed(self):
        toc_text = "目录\n第一章 概述\n1.1 背景\n1.2 目标\n第二章 设计\n2.1 架构\n2.2 模块"
        doc = {"type": "doc", "content": [_heading(1, "目录"), _text(toc_text)]}
        r = chunk(doc, token_counter=FAKE_TC)
        toc_children = [
            c for c in r.chunks if not c.is_parent and c.meta.get("block_type") == "toc"
        ]
        assert toc_children, "目录应识别为单独子块"
        assert toc_children[0].meta.get("skipped_embed") is True, "目录不 embed(ADR §4.5)"
        _assert_hard_metrics(r)

    def test_image_block_skipped_embed(self):
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "流程"),
                {"type": "image", "attrs": {"alt": "架构图"}},
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        imgs = [c for c in r.chunks if c.meta.get("block_type") == "image"]
        assert imgs and imgs[0].meta.get("skipped_embed") is True
        assert "[图片] 架构图" in imgs[0].text
        _assert_hard_metrics(r)


class TestParentChild:
    def test_parent_not_embeddable_and_has_children(self):
        long_para = "闭包是函数与其词法环境的组合。" * 30
        doc = {"type": "doc", "content": [_heading(1, "闭包"), _text(long_para)]}
        r = chunk(doc, token_counter=FAKE_TC)
        parents = [c for c in r.chunks if c.is_parent]
        children = [c for c in r.chunks if not c.is_parent]
        assert parents, "至少一个父块"
        assert children, "至少一个子块"
        # 父块不 embed(is_parent=True 即不进 embeddings,ADR 0003 决策①)
        for p in parents:
            assert p.is_parent is True
        # heading_path 回填
        assert r.sections[0].heading_path == "闭包"
        assert children[0].heading_path == "闭包"
        _assert_hard_metrics(r)


class TestMergeAntiFragmentation:
    """模拟《闭包.md》结构:代码块密集 + 小标题密集,验证 §4.7 合并修碎片化。"""

    def test_dense_code_and_small_headings_merge(self):
        # 10 个微型代码块塞进一个大节(总字数 ≥ MIN),周围两个一句话小节
        code_blocks = [
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f'def func_{i}():\n    """第 {i} 个函数说明。"""\n'
                            f"    x = i * 2\n    return x + 1\n"
                        ),
                    }
                ],
            }
            for i in range(10)
        ]
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "小节A"),
                _text("一句话说明。"),
                _heading(1, "工具集"),
                *code_blocks,
                _heading(1, "小节B"),
                _text("又一句话。"),
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        # 不合并会是 10 个代码块 + 2 个小节块;合并后代码块明显减少
        code_children = [c for c in r.chunks if c.meta.get("block_type") == "code"]
        assert len(code_children) <= 4, f"合并后代码块应明显减少,实际 {len(code_children)}"
        # 工具集 section 够大(≥ MIN),其块计入 in_range 分母;合并后应多数落在 250-400
        assert r.metrics["chunks_in_range_ratio"] >= 0.6, (
            f"in_range={r.metrics['chunks_in_range_ratio']}"
        )
        _assert_hard_metrics(r)

    def test_merge_never_crosses_section(self):
        # 两个小节各一个超短块:都不够 MIN,但绝不跨 section 合并(heading_path 语义)
        doc = {
            "type": "doc",
            "content": [
                _heading(1, "甲"),
                _text("甲的一句话。"),
                _heading(1, "乙"),
                _text("乙的一句话。"),
            ],
        }
        r = chunk(doc, token_counter=FAKE_TC)
        for sec in r.sections:
            for ci in sec.chunk_indices:
                assert r.chunks[ci].heading_path == sec.heading_path
        _assert_hard_metrics(r)
