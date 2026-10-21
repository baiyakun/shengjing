"""LLM 调用：OpenAI 兼容接口（本地 mlx-lm 服务或任意云端服务）+ 结构化 JSON 输出。"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError


class ChatClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str: ...


class LLMError(RuntimeError):
    pass


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._headers = headers

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=self._headers
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"无法连接 LLM 服务：{exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"LLM 服务返回错误 HTTP {resp.status_code}：{resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("LLM 返回格式无法识别") from exc


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
    max_tokens: int = 8192,
) -> T:
    """要求 LLM 输出符合 model_cls 的 JSON；解析或校验失败时把错误反馈给模型重试。"""
    history = list(messages)
    last_error = ""
    for _ in range(retries + 1):
        raw = await client.chat(history, temperature=temperature, max_tokens=max_tokens)
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
