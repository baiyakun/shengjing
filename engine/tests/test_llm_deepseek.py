"""兼容 DeepSeek（和其他 OpenAI 兼容接口）：请求参数、思考模式、JSON 模式、错误处理。"""

import json

import httpx
import pytest
from pydantic import BaseModel

from shengjing_engine.orchestration.llm import (
    DEEPSEEK,
    GENERIC,
    LLMError,
    OpenAICompatClient,
    chat_json,
    dialect_for,
    normalize_base_url,
)


class FakeAPI:
    """按顺序返回预设的响应，记录收到的请求。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.urls.append(str(request.url))
        item = self.responses.pop(0)
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


def answer(content, *, finish="stop", reasoning=None):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "message": message, "finish_reason": finish}]}


def client(api: FakeAPI, base_url="https://api.deepseek.com", **kw) -> OpenAICompatClient:
    return OpenAICompatClient(base_url, "deepseek-flash", "sk-test",
                              transport=httpx.MockTransport(api), retry_delays=(0, 0), **kw)


class Reply(BaseModel):
    title: str


def test_dialect_and_base_url():
    assert dialect_for("https://api.deepseek.com") is DEEPSEEK
    assert dialect_for("https://api.deepseek.com/v1") is DEEPSEEK
    assert dialect_for("https://api.openai.com/v1") is GENERIC
    assert dialect_for("http://127.0.0.1:8080/v1") is GENERIC
    assert normalize_base_url(" https://api.deepseek.com/v1/chat/completions/ ") == \
        "https://api.deepseek.com/v1"


async def test_deepseek_turns_thinking_off_and_uses_json_mode():
    api = FakeAPI(answer('{"title": "雨夜"}'))
    result = await chat_json(client(api), [{"role": "user", "content": "输出 JSON"}], Reply)
    assert result.title == "雨夜"
    sent = api.requests[0]
    assert api.urls[0] == "https://api.deepseek.com/chat/completions"
    assert sent["model"] == "deepseek-flash"
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["max_tokens"] == 16384


async def test_deepseek_thinking_mode_leaves_room_for_reasoning():
    api = FakeAPI(answer('{"title": "雨夜"}', reasoning="先想一想……"))
    result = await chat_json(client(api, thinking=True), [{"role": "user", "content": "JSON"}],
                             Reply)
    assert result.title == "雨夜"  # 只取最终答案，不取思考过程
    sent = api.requests[0]
    assert sent["thinking"] == {"type": "enabled"}
    assert sent["reasoning_effort"] == "high"
    assert sent["max_tokens"] >= 65536
    assert "temperature" not in sent


async def test_other_services_get_plain_openai_requests():
    api = FakeAPI(answer('{"title": "x"}'))
    await chat_json(client(api, base_url="https://api.example.com/v1"),
                    [{"role": "user", "content": "JSON"}], Reply)
    sent = api.requests[0]
    assert "thinking" not in sent and "response_format" not in sent
    assert sent["temperature"] == 0.4


async def test_empty_answer_is_retried_without_polluting_history():
    api = FakeAPI(answer(""), answer(None), answer('{"title": "好"}'))
    result = await chat_json(client(api), [{"role": "user", "content": "JSON"}], Reply)
    assert result.title == "好"
    assert len(api.requests) == 3
    # 重试时原样再请求，不把空回复塞进对话历史
    assert all(len(r["messages"]) == 1 for r in api.requests)


async def test_truncated_output_explains_what_to_do():
    api = FakeAPI(answer('{"title": "被截', finish="length"))
    with pytest.raises(LLMError, match="长度上限.*关闭深度思考"):
        await client(api, thinking=True).chat([{"role": "user", "content": "hi"}])


async def test_content_filter_is_explained():
    api = FakeAPI(answer("", finish="content_filter"))
    with pytest.raises(LLMError, match="安全审核"):
        await client(api).chat([{"role": "user", "content": "hi"}])


@pytest.mark.parametrize(("status", "expected"), [
    (401, "API Key 不对"),
    (402, "余额不足"),
    (404, "接口地址或模型名不对"),
    (422, "拒绝了请求.*bad param"),
])
async def test_errors_are_readable(status, expected):
    api = FakeAPI(httpx.Response(status, json={"error": {"message": "bad param"}}))
    with pytest.raises(LLMError, match=expected):
        await client(api).chat([{"role": "user", "content": "hi"}])
    assert len(api.requests) == 1  # 这些错误重试也没用


async def test_busy_server_is_retried():
    api = FakeAPI(httpx.Response(503, json={"error": {"message": "overloaded"}}),
                  httpx.Response(429, json={}), answer("你好"))
    assert await client(api).chat([{"role": "user", "content": "hi"}]) == "你好"
    assert len(api.requests) == 3


async def test_busy_server_gives_up_with_a_clear_message():
    api = FakeAPI(*[httpx.Response(503, json={}) for _ in range(3)])
    with pytest.raises(LLMError, match="暂时不可用"):
        await client(api).chat([{"role": "user", "content": "hi"}])


async def test_connection_test_uses_unsaved_values(services):
    api = FakeAPI(answer("你好"), httpx.Response(401, json={}))
    services.llm_transport = httpx.MockTransport(api)
    result = await services.test_llm(base_url="https://api.deepseek.com", model="deepseek-v4-pro",
                                     api_key="sk-new")
    assert result["ok"] and result["reply"] == "你好" and result["model"] == "deepseek-v4-pro"
    assert api.requests[0]["thinking"] == {"type": "disabled"}
    failed = await services.test_llm(base_url="https://api.deepseek.com", model="deepseek-flash")
    assert failed == {"ok": False, "error": "LLM 的 API Key 不对或没有权限，请在设置 → LLM 里检查"}
    missing = await services.test_llm()
    assert not missing["ok"] and "接口地址和模型名" in missing["error"]


def test_connection_test_api(services):
    from fastapi.testclient import TestClient

    from shengjing_engine.api.app import create_app

    api = FakeAPI(answer("你好"))
    services.llm_transport = httpx.MockTransport(api)
    services.settings.update({"llm": {"provider": "cloud", "cloud_base_url": "https://api.deepseek.com",
                                      "cloud_model": "deepseek-flash", "cloud_thinking": True}})
    with TestClient(create_app(services, run_background=False)) as c:
        r = c.post("/llm/test", json={}, headers={"Authorization": "Bearer test-token"})
    assert r.json()["ok"] is True
    assert api.requests[0]["thinking"] == {"type": "enabled"}
