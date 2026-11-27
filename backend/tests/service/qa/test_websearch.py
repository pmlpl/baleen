"""websearch 两级链单测(Tavily 主力 + DeepSeek Search 兜底,2026-11-23):

覆盖:Tavily 成功不降级(自动回归主力)/Tavily 432→DeepSearch 兜底(响应映射+留痕)/
两级皆败向上抛/base_url 归一化(带与不带 /v1、带尾斜杠)/非 deepseek 网关兜底不可用/
未配 llm key 兜底不可用。HTTP 全 mock(urlopen 替身),不打真网。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.service.qa.websearch import TAVILY_ENDPOINT, search_web

_DS_KEY = "sk-ds-test-key"
_DS_BASE = "https://api.deepseek.com/v1"  # owner 实配形态(带 /v1)
# Tavily 响应里 content 也会被同口径截断;这里只测映射,主力行为回归见上批
_DS_BODY = {
    "results": [
        {
            "title": "药典条目",
            "site_name": "example",
            "url": "https://example.com/drug",
            "summary": "正文" * 900,  # 超 1500,验截断
            "snippet": "摘要",
            "content": "备用正文",
        },
        {
            "title": "无 summary 条目",
            "url": "https://example.com/two",
            "snippet": "备用摘要",
        },
        {
            "title": "多余条目(max_results 应截掉)",
            "url": "https://example.com/three",
            "summary": "第三条",
        },
    ]
}


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _install(monkeypatch: pytest.MonkeyPatch, behaviors: list[bytes | Exception]) -> list[dict]:
    """替身 urlopen:按序消费 behaviors(异常=抛,bytes=响应体),记录每次请求。"""
    calls: list[dict] = []
    queue = iter(behaviors)

    def fake_urlopen(req: urllib.request.Request, timeout: float = -1) -> _FakeResp:
        calls.append(
            {
                "url": req.full_url,
                "body": json.loads((req.data or b"{}").decode("utf-8")),
                "headers": dict(req.headers),
                "timeout": timeout,
            }
        )
        act = next(queue)
        if isinstance(act, Exception):
            raise act
        return _FakeResp(act)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _http_432() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(TAVILY_ENDPOINT, 432, "Quota Exceeded", None, None)


def test_tavily_success_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tavily 成功 → 不碰 DeepSearch(配额恢复后零配置自动回归主力)。"""
    tavily_body = json.dumps(
        {"results": [{"title": "t", "url": "https://t.example", "content": "片段"}]}
    ).encode("utf-8")
    calls = _install(monkeypatch, [tavily_body])

    meta: dict = {}
    results = search_web(
        "查询",
        api_key="tvly-x",
        fallback_base_url=_DS_BASE,
        fallback_api_key=_DS_KEY,
        meta_out=meta,
    )

    assert len(calls) == 1  # 只打了 Tavily
    assert calls[0]["url"] == TAVILY_ENDPOINT
    assert calls[0]["body"]["api_key"] == "tvly-x"
    assert results == [{"title": "t", "url": "https://t.example", "content": "片段"}]
    assert "fallback" not in meta


def test_tavily_432_degrades_to_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tavily 432(配额)→ 自动降级 DeepSearch,summary 为主内容字段,留痕进 meta_out。"""
    calls = _install(
        monkeypatch,
        [_http_432(), json.dumps(_DS_BODY).encode("utf-8")],
    )

    meta: dict = {}
    results = search_web(
        "查询",
        api_key="tvly-x",
        max_results=2,  # 3 条只留前 2,钉住 max_results 传透
        fallback_base_url=_DS_BASE,
        fallback_api_key=_DS_KEY,
        meta_out=meta,
    )

    assert len(calls) == 2
    # DeepSeek 侧:Bearer 认证、body 只有 query、results 映射到 {title,url,content}
    assert calls[1]["url"] == "https://api.deepseek.com/v1/tools/search"
    assert calls[1]["headers"]["Authorization"] == f"Bearer {_DS_KEY}"
    assert calls[1]["body"] == {"query": "查询"}
    assert meta["fallback"] == "Tavily HTTP 432→DeepSearch 兜底"
    # 与 Tavily 同口径:content 取 summary 为主,1500 截断,max_results 截条数
    assert [r["title"] for r in results] == ["药典条目", "无 summary 条目"]
    assert results[0]["url"] == "https://example.com/drug"
    assert results[0]["content"] == "正文" * 750  # 1500 字符截断
    assert results[1]["content"] == "备用摘要"  # summary 缺失 → content → snippet 兜底


def test_base_url_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url 带不带 /v1、带不带尾斜杠都要归一化到 {root}/v1/tools/search。"""
    ds_body = json.dumps({"results": []}).encode("utf-8")
    for base, expected in [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/tools/search"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/tools/search"),
        # LLMClient.base_url 会补尾斜杠,这条钉住它也能用
        ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/tools/search"),
    ]:
        calls = _install(monkeypatch, [_http_432(), ds_body])
        search_web("查询", api_key="tvly-x", fallback_base_url=base, fallback_api_key=_DS_KEY)
        assert calls[1]["url"] == expected, base


def test_both_fail_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """两级皆败 → 向上抛(调用方走自答口径),meta_out 不留降级痕。"""
    _install(monkeypatch, [_http_432(), urllib.error.URLError("conn reset")])
    meta: dict = {}
    with pytest.raises(urllib.error.URLError):
        search_web(
            "查询",
            api_key="tvly-x",
            fallback_base_url=_DS_BASE,
            fallback_api_key=_DS_KEY,
            meta_out=meta,
        )
    assert "fallback" not in meta


@pytest.mark.parametrize(
    "base_url",
    ["https://llm-gateway.internal/v1", "", None],
)
def test_non_deepseek_fallback_unavailable(
    monkeypatch: pytest.MonkeyPatch, base_url: str | None
) -> None:
    """非 deepseek 网关(base_url 不含 deepseek)→ 兜底不可用,抛 Tavily 原错误。"""
    calls = _install(monkeypatch, [_http_432()])
    with pytest.raises(urllib.error.HTTPError) as ei:
        search_web("查询", api_key="tvly-x", fallback_base_url=base_url, fallback_api_key=_DS_KEY)
    assert ei.value.code == 432
    assert len(calls) == 1  # 没碰 DeepSearch


def test_missing_fallback_key_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """owner 没配自己的 llm key → 兜底同样不可用(直接抛)。"""
    calls = _install(monkeypatch, [_http_432()])
    with pytest.raises(urllib.error.HTTPError):
        search_web("查询", api_key="tvly-x", fallback_base_url=_DS_BASE, fallback_api_key="")
    assert len(calls) == 1


def test_tavily_empty_results_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tavily 200 但空结果 = 成功(不降级;免费主力只是没搜到)。"""
    calls = _install(monkeypatch, [json.dumps({"results": []}).encode("utf-8")])
    results = search_web(
        "查询", api_key="tvly-x", fallback_base_url=_DS_BASE, fallback_api_key=_DS_KEY
    )
    assert results == []
    assert len(calls) == 1


def test_no_key_leaks_into_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """留痕纪律:降级日志可看原因,但 api_key 永不进日志。"""
    _install(monkeypatch, [_http_432(), json.dumps({"results": []}).encode("utf-8")])
    with caplog.at_level("WARNING", logger="app.service.qa.websearch"):
        search_web(
            "查询", api_key="tvly-secret", fallback_base_url=_DS_BASE, fallback_api_key=_DS_KEY
        )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "DeepSearch 兜底" in text  # 降级原因可见(配额恢复与否看日志)
    assert "tvly-secret" not in text
    assert _DS_KEY not in text
