"""G1 价值闸单测(合成 fixture,不碰 eval/samples)。

核心约束(AGENTS §5 决策 8):误拒代价 ≫ 误收代价 → 只拦明确垃圾,
3 行真实信息、英文论文都不许 reject。
"""

from app.service.gate import judge
from app.service.parse import ParseResult, ParseStatus
from app.service.parse.blocks import heading, paragraph_from_text


def _txt_pr(text: str) -> ParseResult:
    blocks = [paragraph_from_text(ln) for ln in text.splitlines() if ln.strip()]
    return ParseResult(parse_status=ParseStatus.OK, blocks={"type": "doc", "content": blocks})


def _pr(*blocks) -> ParseResult:
    return ParseResult(parse_status=ParseStatus.OK, blocks={"type": "doc", "content": list(blocks)})


class TestG1Junk:
    def test_hello_world_reject(self):
        g = judge(_txt_pr("hello, world!"))
        assert g.verdict == "reject"
        assert g.quality == "junk"
        assert g.action == "drop"

    def test_empty_file_reject(self):
        g = judge(ParseResult(parse_status=ParseStatus.OK, blocks={"type": "doc", "content": []}))
        assert g.verdict == "reject"
        assert g.quality == "junk"

    def test_boilerplate_chinese_reject(self):
        g = judge(_txt_pr("这是一个测试文件"))
        assert g.verdict == "reject"
        assert g.quality == "junk"


class TestG1Accept:
    def test_long_note_accept(self):
        body = "闭包是函数与其词法环境的组合,持有不再需要的引用会导致内存泄漏。" * 200
        g = judge(_pr(heading(1, "闭包的坑"), paragraph_from_text(body)))
        assert g.verdict == "accept"
        assert g.action == "index"
        assert g.quality in {"core", "normal", "reference"}

    def test_three_line_real_info_must_accept(self):
        """关键反例:短但有真实信息,不许 reject(误拒代价 ≫ 误收代价)。"""
        g = judge(
            _txt_pr(
                "内存泄漏在闭包持有引用时发生。\n"
                "解决:释放不再需要的引用。\n"
                "注意:Chrome devtools 可排查内存。"
            )
        )
        assert g.verdict == "accept", f"3 行真实信息被误拒:{g.reasons}"

    def test_english_paper_not_rejected_for_being_english(self):
        """中文占比只记录不判定:英文论文不能因中文占比 0 被 reject。"""
        g = judge(
            _txt_pr(
                "Closures capture the lexical environment. "
                "Memory leaks occur when closures retain unused references. "
                "Use Chrome devtools to inspect retained objects."
            )
        )
        assert g.verdict == "accept", f"英文被误拒:{g.reasons}"
        assert g.meta.get("cjk_ratio") == 0.0
