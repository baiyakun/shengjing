import pytest
from pydantic import BaseModel

from shengjing_engine.orchestration.casting import auto_cast
from shengjing_engine.orchestration.llm import LLMError, chat_json, extract_json
from shengjing_engine.orchestration.qa import check_line, error_rate
from shengjing_engine.orchestration.script import CastMember


def test_extract_json_handles_thinking_and_fences():
    raw = '<think>先想想 {"no": 1}</think>\n好的：\n```json\n{"a": [1, 2], "b": "x}"}\n```'
    assert extract_json(raw) == {"a": [1, 2], "b": "x}"}


def test_extract_json_finds_first_balanced_object():
    assert extract_json('结果如下 {"a": {"b": 1}} 以上') == {"a": {"b": 1}}


class Out(BaseModel):
    name: str


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.seen.append(messages)
        return self.replies.pop(0)


async def test_chat_json_retries_with_error_feedback():
    llm = ScriptedLLM(['{"wrong": 1}', '{"name": "ok"}'])
    out = await chat_json(llm, [{"role": "user", "content": "x"}], Out)
    assert out.name == "ok"
    assert "不符合要求" in llm.seen[1][-1]["content"]


async def test_chat_json_gives_up():
    llm = ScriptedLLM(["nope"] * 3)
    with pytest.raises(LLMError):
        await chat_json(llm, [], Out, retries=2)


def test_error_rate_chinese_chars_and_punctuation():
    assert error_rate("你好，世界！", "你好世界") == 0.0
    assert error_rate("你好世界", "你好") == pytest.approx(0.5)
    assert error_rate("hello big world", "hello world") == pytest.approx(1 / 3)


def test_check_line_flags_dropped_words_and_duration():
    r = check_line("今天天气很好", "今天", 1.0, target_duration=3.0)
    assert not r.passed
    assert len(r.issues) == 2
    assert check_line("今天天气很好", "今天天气很好。", 2.0).passed


def test_auto_cast_matches_gender_and_keeps_explicit_choice():
    cast = [
        CastMember(id="a", name="林小雨", description="二十岁女生，活泼"),
        CastMember(id="b", name="老王", description="中年男人，沉稳"),
        CastMember(id="c", name="旁白", description="", voice_id="v_fixed"),
    ]
    voices = [
        {"id": "v_m", "name": "低沉男声", "tags": ["男", "中年"]},
        {"id": "v_f", "name": "清亮女声", "tags": ["女", "年轻"]},
        {"id": "v_fixed", "name": "旁白", "tags": []},
    ]
    result = auto_cast(cast, voices)
    assert result == {"a": "v_f", "b": "v_m", "c": "v_fixed"}
