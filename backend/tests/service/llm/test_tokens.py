"""token 估算与截断的单测(纯函数,无 IO)。"""

from app.service.llm import estimate_tokens, truncate_to_tokens


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_cjk_one_token_per_char(self):
        assert estimate_tokens("你好世界") == 4

    def test_ascii_uses_ratio(self):
        assert estimate_tokens("abcdefg") == 2  # ceil(7/3.5)

    def test_mixed(self):
        # 2 CJK + 7 ASCII → 2 + 2 = 4
        assert estimate_tokens("你好" + "abcdefg") == 4


class TestTruncate:
    def test_no_truncation(self):
        text, cut = truncate_to_tokens("短文本", 100)
        assert text == "短文本"
        assert cut is False

    def test_truncates_within_budget(self):
        text = "知" * 1000
        out, cut = truncate_to_tokens(text, 100)
        assert cut is True
        assert estimate_tokens(out) <= 100
        assert out == text[:100]

    def test_zero_budget(self):
        text, cut = truncate_to_tokens("abc", 0)
        assert text == ""
        assert cut is True
