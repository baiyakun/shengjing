"""LLM 调用：OpenAI 兼容接口（本地 mlx-lm 服务或任意云端服务）+ 结构化 JSON 输出。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError


class ChatClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_output: bool = False,
    ) -> str: ...


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Dialect:
    """各家 OpenAI 兼容接口的差异。"""

    name: str
    json_mode: bool = False
    """支持 response_format={"type": "json_object"}。"""
    thinking_switch: bool = False
    """DeepSeek 式的深度思考开关：thinking={"type": "enabled"|"disabled"}，默认开启。"""


GENERIC = Dialect("generic")
DEEPSEEK = Dialect("deepseek", json_mode=True, thinking_switch=True)

# DeepSeek 当前的模型名（2026 年 10 月核对：https://api-docs.deepseek.com）
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")

# 深度思考时，思考内容也算在 max_tokens 里，给足空间，免得答案被挤掉
THINKING_MAX_TOKENS = 65536
# 服务商限流、繁忙时自动重试的等待时间（秒）
RETRY_DELAYS = (2.0, 6.0)
RETRY_STATUS = {429, 500, 502, 503, 504}


def dialect_for(base_url: str) -> Dialect:
    host = (urlparse(base_url).hostname or "").lower()
    if host == "deepseek.com" or host.endswith(".deepseek.com"):
        return DEEPSEEK
    return GENERIC


def normalize_base_url(url: str) -> str:
    """接受用户可能填的各种写法：去掉空格、末尾的 / 和 /chat/completions。"""
    url = url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    return url.rstrip("/")


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if data.get("message"):
            return str(data["message"])[:300]
    return resp.text[:200]


def http_error_message(resp: httpx.Response) -> str:
    status = resp.status_code
    detail = _error_detail(resp)
    if status in (401, 403):
        return "LLM 的 API Key 不对或没有权限，请在设置 → LLM 里检查"
    if status == 402:
        return "LLM 服务的账户余额不足，请到服务商那里充值"
    if status == 404:
        return f"LLM 的接口地址或模型名不对（HTTP 404）：{detail}"
    if status == 429:
        return "请求太频繁或超出并发限制，被 LLM 服务商限流了，请稍后再试"
    if status in RETRY_STATUS:
        return f"LLM 服务暂时不可用（HTTP {status}，服务器繁忙），已经重试过，请稍后再试"
    return f"LLM 服务拒绝了请求（HTTP {status}）：{detail}"


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        thinking: bool = False,
        dialect: Dialect | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.model = model.strip()
        self.thinking = thinking
        self.dialect = dialect or dialect_for(self.base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        self._headers = headers
        self._transport = transport
        self._retry_delays = retry_delays

    def build_payload(
        self, messages: list[dict[str, str]], *, temperature: float, max_tokens: int,
        json_output: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.dialect.thinking_switch:
            if self.thinking:
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = "high"
                payload.pop("temperature")  # 思考模式下不起作用
                payload["max_tokens"] = max(max_tokens, THINKING_MAX_TOKENS)
            else:
                # DeepSeek 默认开启思考；剧本、纪要这类任务关掉更快更省
                payload["thinking"] = {"type": "disabled"}
        if json_output and self.dialect.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        delays = [*self._retry_delays, None]
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=10.0), transport=self._transport
        ) as client:
            for delay in delays:
                try:
                    resp = await client.post(url, json=payload, headers=self._headers)
                except httpx.TransportError as exc:
                    if delay is None:
                        raise LLMError(f"无法连接 LLM 服务：{exc}") from exc
                else:
                    if resp.status_code not in RETRY_STATUS or delay is None:
                        return resp
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_output: bool = False,
    ) -> str:
        payload = self.build_payload(messages, temperature=temperature, max_tokens=max_tokens,
                                     json_output=json_output)
        resp = await self._post(payload)
        if resp.status_code >= 400:
            raise LLMError(http_error_message(resp))
        try:
            choice = resp.json()["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM 返回格式无法识别") from exc
        # 思考模式的推理过程在 reasoning_content 里，这里只要最终答案
        content = message.get("content") or ""
        finish = choice.get("finish_reason")
        if finish == "length":
            tip = "，或者在设置 → LLM 里关闭深度思考" if self.thinking else ""
            raise LLMError(f"LLM 的输出达到长度上限被截断了。可以把原文分成几段再试{tip}")
        if finish == "content_filter":
            raise LLMError("内容被 LLM 服务商的安全审核拦截了，可以调整原文后再试")
        if finish == "insufficient_system_resource":
            raise LLMError("LLM 服务商资源不足，生成被中断，请稍后再试")
        return content


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """去掉推理模型输出的 <think>…</think> 片段。"""
    text = _THINK_RE.sub("", text)
    # 只有结束标签的情况（部分模型会省略开始标签）
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def extract_json(text: str) -> Any:
    """从模型输出里取出第一个完整的 JSON 对象或数组。"""
    text = strip_thinking(text)
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            return json.loads(candidate)
        except ValueError:
            text = candidate
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            end = _matching_bracket(text, start, opener, closer)
            if end is None:
                break
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                start = text.find(opener, start + 1)
    raise ValueError("输出里没有找到 JSON")


def _matching_bracket(text: str, start: int, opener: str, closer: str) -> int | None:
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


async def chat_json[T: BaseModel](
    client: ChatClient,
    messages: list[dict[str, str]],
    model_cls: type[T],
    *,
    retries: int = 2,
    temperature: float = 0.4,
    max_tokens: int = 16384,
) -> T:
    """要求 LLM 输出符合 model_cls 的 JSON；解析或校验失败时把错误反馈给模型重试。"""
    history = list(messages)
    last_error = ""
    for _ in range(retries + 1):
        raw = await client.chat(history, temperature=temperature, max_tokens=max_tokens,
                                json_output=True)
        if not raw.strip():
            # 部分服务（例如 DeepSeek 的 JSON 模式）偶尔返回空内容，原样再请求一次
            last_error = "LLM 返回了空内容"
            continue
        try:
            return model_cls.model_validate(extract_json(raw))
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:1500]
            history = [
                *history,
                {"role": "assistant", "content": raw[:6000]},
                {
                    "role": "user",
                    "content": "上面的输出不符合要求，错误如下。请只输出修正后的完整 JSON，"
                    f"不要输出任何解释：\n{last_error}",
                },
            ]
    raise LLMError(f"LLM 多次输出都不符合格式要求：{last_error}")
