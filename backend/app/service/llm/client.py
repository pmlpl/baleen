"""LLM adapter(OpenAI 兼容协议,AGENTS.md 决策 9)。

只做「一次 chat 调用」+ 能力探测,不含任何业务判定:
- 超时:默认 60s
- 重试:仅网络错误 / 5xx,**最多 2 次**(不含首次)
- 错误分类:认证失败 / 超时 / 限流 / 网络 / 其它
- ``api_key`` 永不进日志、永不出现在异常文本里

实现零第三方依赖(标准库 urllib),transport 可注入以便测试完全不联网。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any, Protocol, cast
from urllib.parse import urljoin

from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings

DEFAULT_TIMEOUT = 60.0  # 秒
DEFAULT_MAX_RETRIES = 2  # 仅网络/5xx/429;总尝试次数 = 1 + 该值
DEFAULT_MAX_TOKENS = 800
# 重试退避基准(秒),按 0.5 / 1.0 递增;测试注入 no-op sleep
RETRY_BACKOFF = 0.5
# 429 的退避:优先读 Retry-After,缺失时按 5s / 15s(本机网关「上游账号冷却 60 秒」)
RATE_LIMIT_BACKOFF = (5.0, 15.0)
MAX_RATE_LIMIT_WAIT = 90.0  # 单次等待上限,避免一个请求挂太久


# ---- 错误分类(调用方按类型决定:重试 / 提示用户 / 降级)----
class LLMError(Exception):
    """所有 LLM 异常的基类。``status_code`` 为 HTTP 状态码(网络错误时为 None)。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMAuthError(LLMError):
    """401 / 403:key 无效或无权访问。"""


class LLMTimeoutError(LLMError):
    """请求超时(含重试后仍超时)。"""


class LLMRateLimitError(LLMError):
    """429:限流;尊重 Retry-After 重试 2 次后仍失败才抛。"""


class LLMNetworkError(LLMError):
    """DNS / 连接失败 / 连接被重置等网络错误。"""


class LLMResponseError(LLMError):
    """其它 HTTP 错误(4xx/5xx 重试耗尽)或响应体不是合法 OpenAI 结构。"""


class Transport(Protocol):
    """一次 HTTP 请求的最小抽象;测试注入 fake,生产用 urllib。"""

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        """返回 (status_code, response_headers, response_body);网络异常直接抛出。"""
        ...


class UrllibTransport:
    """标准库实现;HTTP 错误码不抛异常,而是作为返回值交给上层分类。"""

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:  # 4xx/5xx 也是合法响应,由上层分类
            return exc.code, dict((exc.headers or {}).items()), exc.read()

    def request_stream(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], Any]:
        """流式请求:第三元素是**未读尽的响应对象**(可逐行迭代),调用方负责 close。"""
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.status, dict(resp.headers.items()), resp
        except urllib.error.HTTPError as exc:
            # 流式的 HTTP 错误也在打开阶段暴露;exc 自身可 read,交上层分类
            return exc.code, dict((exc.headers or {}).items()), exc


class ChatResult(BaseModel):
    """一次 chat 调用的结果与计费信息。"""

    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    # DeepSeek 系:前缀缓存命中/未命中的 prompt token 数(OpenAI 系无此字段 → None)
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    latency_ms: float = 0.0


class LLMCapabilities(BaseModel):
    """probe() 的能力标记。"""

    model: str
    models_endpoint: bool  # GET /models 是否可用
    chat: bool  # chat completions 是否可用
    json_mode: bool  # 是否接受 response_format={"type":"json_object"}
    latency_ms: float = 0.0
    notes: list[str] = Field(default_factory=list)


class LLMClient:
    """OpenAI 兼容 chat client。

    参数全可注入;``from_settings()`` 从 ``config`` 读 `llm_base_url/llm_api_key/llm_model`。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url:
            raise LLMError("未配置 LLM_BASE_URL")
        if not api_key:
            raise LLMAuthError("未配置 LLM_API_KEY")
        if not model:
            raise LLMError("未配置 LLM_MODEL")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._transport: Transport = transport or UrllibTransport()
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        s = settings or get_settings()
        return cls(base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model)

    # ---- 对外:chat ----
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        response_format: dict | None = None,
    ) -> ChatResult:
        """一次 chat completions 调用;失败按错误分类抛异常。"""
        if not messages:
            raise LLMError("messages 不能为空")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        started = time.perf_counter()
        raw = self._request("POST", "chat/completions", payload)
        result = self._parse_chat(raw)
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return result

    # ---- 对外:流式 chat ----
    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[dict[str, Any]]:
        """流式 chat:逐段 yield 事件字典,消费方按 ``type`` 分流。

        - {"type": "reasoning", "text": ...} —— 推理模型的思考过程(不想展示可忽略)
        - {"type": "content", "text": ...}   —— 正文增量
        - {"type": "usage", ...}             —— 末尾计费/缓存块(上游未命中可能缺席)

        流式不做重试(中途重试无法续传);打开失败(认证/限流/5xx)在首个 yield 前抛出。
        同步生成器;async 调用方用 to_thread 逐段推进或自行包任务。
        """
        if not messages:
            raise LLMError("messages 不能为空")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        url = urljoin(self.base_url, "chat/completions")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        stream_fn = getattr(self._transport, "request_stream", None)
        if stream_fn is None:  # 旧自定义 Transport 未实现流式
            raise LLMError("当前 Transport 不支持流式(request_stream 缺失)")
        status, _headers, resp = cast(Any, stream_fn("POST", url, headers, body, self.timeout))
        if status in (401, 403):
            raw = resp.read() if hasattr(resp, "read") else b""
            raise LLMAuthError(
                f"认证失败(HTTP {status}):请检查 LLM_API_KEY", status_code=status
            )
        if status == 429:
            raise LLMRateLimitError("触发限流(HTTP 429)", status_code=status)
        if not (200 <= status < 300):
            raw = resp.read() if hasattr(resp, "read") else b""
            raise LLMResponseError(f"服务端错误(HTTP {status}):{_short(raw)}", status_code=status)

        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[len("data: "):]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue  # 半行/心跳,忽略
                usage = chunk.get("usage")
                if usage:  # 末尾计费块(choices 为空)
                    hit, miss = _cache_fields(usage)
                    yield {
                        "type": "usage",
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "prompt_cache_hit_tokens": hit,
                        "prompt_cache_miss_tokens": miss,
                    }
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                piece = delta.get("content")
                if piece:
                    yield {"type": "content", "text": piece}
        finally:
            close = getattr(resp, "close", None)
            if close is not None:
                close()

    # ---- 对外:能力探测 ----
    def probe(self) -> LLMCapabilities:
        """用最便宜的调用验证 key 有效,并返回能力标记。

        1) GET /models(不消耗 token);部分兼容服务没实现该端点,404 视为「不可用」而非失败
        2) 最小 chat 调用(max_tokens=8)确认 chat 可用;顺带试 response_format
           - 若被 400/422 拒绝,去掉 response_format 再试一次,标记 json_mode=False
        """
        started = time.perf_counter()
        notes: list[str] = []
        models_ok = False
        try:
            self._request("GET", "models", None)
            models_ok = True
        except LLMAuthError:
            raise  # key 无效必须让调用方知道
        except LLMError as exc:
            notes.append(f"GET /models 不可用({type(exc).__name__}),改用 chat 探测")

        json_mode = False
        probe_messages = [{"role": "user", "content": '只回复一个 JSON 对象,内容为 {"ok": true}'}]
        try:
            self.chat(
                probe_messages,
                max_tokens=8,
                response_format={"type": "json_object"},
            )
            json_mode = True
        except LLMResponseError as exc:
            if exc.status_code not in (400, 422):
                raise
            notes.append("response_format=json_object 不被接受,标记 json_mode=False")
            self.chat(probe_messages, max_tokens=8)

        return LLMCapabilities(
            model=self.model,
            models_endpoint=models_ok,
            chat=True,
            json_mode=json_mode,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=notes,
        )

    # ---- 内部:HTTP + 重试 + 错误分类 ----
    def _request(self, method: str, path: str, payload: dict | None) -> bytes:
        url = urljoin(self.base_url, path)
        body = None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            try:
                status, resp_headers, raw = self._transport.request(
                    method, url, headers, body, self.timeout
                )
            except (TimeoutError, urllib.error.URLError) as exc:
                if isinstance(exc, TimeoutError) or isinstance(exc.reason, TimeoutError):
                    error: LLMError = LLMTimeoutError(f"请求超时({self.timeout:.0f}s):{exc}")
                else:
                    error = LLMNetworkError(f"网络错误:{exc.reason}")
                if attempt >= self.max_retries:
                    raise error from exc
                self._sleep(RETRY_BACKOFF * (attempt + 1))
                continue

            if 200 <= status < 300:
                return raw
            if status in (401, 403):
                raise LLMAuthError(
                    f"认证失败(HTTP {status}):请检查 LLM_API_KEY", status_code=status
                )
            if status == 429:
                if attempt >= self.max_retries:
                    raise LLMRateLimitError(
                        f"触发限流(HTTP 429),已重试 {self.max_retries} 次仍失败",
                        status_code=status,
                    )
                self._sleep(_retry_after_seconds(resp_headers, attempt))
                continue
            if 500 <= status < 600:
                if attempt >= self.max_retries:
                    raise LLMResponseError(
                        f"服务端错误(HTTP {status}),已重试 {self.max_retries} 次仍失败",
                        status_code=status,
                    )
                self._sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise LLMResponseError(f"请求被拒绝(HTTP {status}):{_short(raw)}", status_code=status)

        raise LLMError("请求重试次数耗尽")  # 理论不可达:每个分支都会 return 或 raise

    def _parse_chat(self, raw: bytes) -> ChatResult:
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"响应不是合法的 OpenAI chat 结构:{exc}") from exc
        if not isinstance(content, str):
            raise LLMResponseError("响应 choices[0].message.content 不是字符串")
        usage = data.get("usage") or {}
        hit, miss = _cache_fields(usage)
        return ChatResult(
            content=content,
            model=str(data.get("model") or self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            prompt_cache_hit_tokens=hit,
            prompt_cache_miss_tokens=miss,
        )


def _retry_after_seconds(headers: dict[str, str], attempt: int) -> float:
    """429 的等待时长:优先 Retry-After(秒),缺失则退避 5s / 15s;单次上限 90s。"""
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw:
        try:
            return max(0.0, min(float(raw), MAX_RATE_LIMIT_WAIT))
        except (TypeError, ValueError):
            pass  # HTTP-date 等非数字格式 → 退回固定退避
    return RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)]


def _cache_fields(usage: dict[str, Any]) -> tuple[int | None, int | None]:
    """缓存字段双格式归一:DeepSeek prompt_cache_* / OpenAI prompt_tokens_details.cached_tokens。

    上游未命中时可能整个不带字段 → (None, None)。
    """
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None:
        details = usage.get("prompt_tokens_details") or {}
        hit = details.get("cached_tokens")
        if hit is not None and miss is None:
            prompt = usage.get("prompt_tokens")
            miss = (prompt - hit) if isinstance(prompt, int) else None
    return hit, miss


def _short(raw: bytes, limit: int = 200) -> str:
    text = raw.decode("utf-8", errors="replace")
    return text[:limit]
