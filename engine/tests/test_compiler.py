import pytest

from shengjing_engine.orchestration.compiler import (
    CompileError,
    VoiceRef,
    compile_script,
    estimate_seconds,
    strip_markup,
)
from shengjing_engine.orchestration.script import Script

SCRIPT = {
    "title": "测试",
    "language": "zh",
    "cast": [
        {"id": "narrator", "name": "旁白", "description": "中年男声"},
        {"id": "c1", "name": "小雨", "description": "年轻女生"},
    ],
    "lines": [
        {"id": "l1", "speaker": "narrator", "text": "夜里下起了雨。", "delivery": "低沉"},
        {"id": "l2", "speaker": "c1", "text": "你怎么才来？", "tags": ["sigh"], "duration": 2.0},
    ],
}

VOICES = {
    "narrator": VoiceRef("/v/n.wav", "这是旁白的参考录音。"),
    "c1": VoiceRef("/v/c1.wav", "这是小雨的参考录音。"),
}


def script() -> Script:
    return Script.model_validate(SCRIPT)


def test_script_rejects_unknown_speaker():
    bad = {**SCRIPT, "lines": [{"id": "l1", "speaker": "nobody", "text": "hi"}]}
    with pytest.raises(ValueError, match="不在角色列表"):
        Script.model_validate(bad)


def test_fish_s2_uses_inline_tags_and_references():
    jobs = compile_script(script(), "fish-s2-pro", VOICES)
    assert jobs[0].text == "[低沉] 夜里下起了雨。"
    assert jobs[1].text == "[sigh] 你怎么才来？"
    assert jobs[1].params["reference_audio"] == "/v/c1.wav"
    assert any("不支持指定时长" in w for w in jobs[1].warnings)


def test_fish_requires_transcript_for_clone():
    voices = {"narrator": VoiceRef("/v/n.wav", ""), "c1": None}
    with pytest.raises(CompileError, match="文字稿"):
        compile_script(script(), "fish-s2-pro", voices)


def test_dots_accepts_audio_only_reference_and_language():
    voices = {"narrator": VoiceRef("/v/n.wav", ""), "c1": None}
    jobs = compile_script(script(), "dots-tts-soar", voices)
    assert jobs[0].params == {"reference_audio": "/v/n.wav", "language": "zh"}
    assert jobs[0].text == "夜里下起了雨。"
    assert any("不支持语气" in w for w in jobs[0].warnings)


def test_drama3_per_line_duration_and_whisper_span():
    s = script()
    s.lines[0].tags = ["whisper"]
    jobs = compile_script(s, "fish-drama-3", VOICES)
    assert jobs[0].text == "[低沉] <whisper>夜里下起了雨。</whisper>"
    assert jobs[1].params["duration_seconds"] == 2.0
    assert jobs[1].text.startswith("[sighs]")


def test_drama3_dialogue_mode_uses_speaker_tokens_and_reference_lists():
    jobs = compile_script(script(), "fish-drama-3", VOICES, dialogue=True)
    assert len(jobs) == 1
    assert jobs[0].line_ids == ["l1", "l2"]
    assert "<|speaker:0|>" in jobs[0].text and "<|speaker:1|>" in jobs[0].text
    assert jobs[0].params["reference_speaker"] == [0, 1]
    assert len(jobs[0].params["reference_audio"]) == 2


def test_moss_ttsd_dialogue_tags_and_chunking():
    s = script()
    jobs = compile_script(s, "moss-ttsd", VOICES, dialogue=True, max_chars=8)
    assert [j.text for j in jobs] == ["[S1]夜里下起了雨。", "[S2]你怎么才来？"]
    assert any("不支持克隆" in w for w in jobs[0].warnings)


def test_breeze_instruction_and_events():
    jobs = compile_script(script(), "breeze-tts-2", {"narrator": None, "c1": None})
    assert jobs[0].params["instruction"] == "低沉"
    assert jobs[0].params["guidance_scale"] == 4.0
    assert jobs[1].text == "(sigh) 你怎么才来？"
    # 没有参考录音时，用角色描述做声音设计
    assert jobs[1].params["instruction"] == "年轻女生"


def test_short_duration_warning():
    s = script()
    s.lines[1].duration = 0.5
    jobs = compile_script(s, "fish-drama-3", VOICES)
    assert any("可能太短" in w for w in jobs[1].warnings)


def test_estimate_seconds_mandarin_rate():
    # 33 个汉字约 10 秒，加 0.3 秒余量
    assert estimate_seconds("字" * 33) == pytest.approx(10.3, abs=0.01)


def test_strip_markup():
    assert strip_markup("<|speaker:0|> [低沉] <whisper>你好</whisper> (sigh) 世界") == "你好 世界"
