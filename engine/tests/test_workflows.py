"""用假模型进程跑通各个工作流（不需要 Mac 和真实模型）。"""

import json

import pytest

from shengjing_engine.audio.ffmpeg import wav_duration, write_wav

from .conftest import FakeScheduler, needs_ffmpeg, tone


class FakeLLM:
    def __init__(self, reply: dict):
        self.reply = reply

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096, json_output=False):
        return "<think>…</think>" + json.dumps(self.reply, ensure_ascii=False)


def use_fake_llm(monkeypatch, reply: dict):
    from contextlib import asynccontextmanager

    from shengjing_engine.workflows import audiobook, meeting, quick

    @asynccontextmanager
    async def session(services):
        yield FakeLLM(reply)

    for mod in (audiobook, meeting, quick):
        monkeypatch.setattr(mod, "llm_session", session)


async def run_task(services, kind, params):
    task = services.tasks.submit(kind, params)
    await services.tasks.run_one(services.tasks.get(task["id"]))
    return services.tasks.get(task["id"])


async def test_quick_tts_with_voice(services, tmp_path):
    ref = write_wav(tmp_path / "ref.wav", tone(1.0), 24000)
    voice = services.voices.create(
        name="我", audio_path=ref, transcript="参考文字", language="zh", tags=[],
        source="imported", consent={"confirmed": True},
    )
    task = await run_task(
        services, "tts.quick", {"text": "你好", "voice_id": voice["id"], "delivery": "开心"}
    )
    assert task["status"] == "succeeded", task["error"]
    model, op, params = services.scheduler.calls[0]
    assert model == "fish-s2-pro"  # 个人版默认
    assert params["text"] == "[开心] 你好"
    assert params["reference_text"] == "参考文字"
    assert wav_duration(task["result"]["path"]) > 0


async def test_commercial_edition_switches_default_and_blocks_fish(services):
    services.settings.update({"edition": "commercial"})
    task = await run_task(services, "tts.quick", {"text": "你好"})
    assert task["status"] == "succeeded"
    assert services.scheduler.calls[0][0] == "dots-tts-soar"
    blocked = await run_task(services, "tts.quick", {"text": "你好", "model": "fish-s2-pro"})
    assert blocked["status"] == "failed"
    assert "不允许在商用版" in blocked["error"]


async def test_task_failure_is_reported_not_raised(services):
    task = await run_task(services, "tts.quick", {"text": ""})
    assert task["status"] == "failed"
    assert task["error"] == "请输入要配音的文字"


SCRIPT = {
    "title": "雨夜",
    "language": "zh",
    "cast": [
        {"id": "narrator", "name": "旁白", "description": "中年男声"},
        {"id": "c1", "name": "小雨", "description": "年轻女生"},
    ],
    "lines": [
        {"id": "l1", "speaker": "narrator", "text": "夜里下起了雨。"},
        {"id": "l2", "speaker": "c1", "text": "你怎么才来？", "delivery": "着急"},
    ],
    "cues": [{"id": "s1", "kind": "sfx", "description": "雨声", "at_line": "l1"}],
}


@needs_ffmpeg
async def test_audiobook_end_to_end(services, monkeypatch):
    use_fake_llm(monkeypatch, SCRIPT)
    gen = await run_task(services, "script.generate", {"text": "原文……", "style": "audiobook"})
    assert gen["status"] == "succeeded", gen["error"]
    pid = gen["result"]["project_id"]

    render = await run_task(services, "audiobook.render", {"project_id": pid, "format": "mp3"})
    assert render["status"] == "succeeded", render["error"]
    result = render["result"]
    assert result["rendered"] == 2 and result["qa_failed"] == []
    assert result["duration"] > 1.0
    srt = open(result["srt"], encoding="utf-8").read()
    assert "旁白：夜里下起了雨。" in srt and "小雨：你怎么才来？" in srt
    used = services.scheduler.used
    assert used[0] == "fish-s2-pro" and "qwen3-asr-1.7b" in used and "moss-sound-effect" in used

    # 再次渲染只重做指定的一句，旧版本保留
    again = await run_task(services, "audiobook.render", {"project_id": pid, "line_ids": ["l2"]})
    assert again["result"]["rendered"] == 1
    renders = services.projects.renders(pid, "l2")
    assert [r["version"] for r in renders] == [1, 2]
    assert [r["selected"] for r in renders] == [False, True]


@needs_ffmpeg
async def test_audiobook_qa_retries_bad_lines(services, monkeypatch):
    use_fake_llm(monkeypatch, SCRIPT)
    gen = await run_task(services, "script.generate", {"text": "原文"})
    pid = gen["result"]["project_id"]

    # 让 ASR 永远“听到”错的内容：质检会重做，最后报告失败的句子
    class BadTranscripts(dict):
        def get(self, key, default=None):
            return "完全不同的内容"

        def setdefault(self, key, default=None):
            return default

    services.scheduler = FakeScheduler(BadTranscripts())
    render = await run_task(services, "audiobook.render", {"project_id": pid, "cues": False})
    assert render["status"] == "succeeded"
    assert sorted(render["result"]["qa_failed"]) == ["l1", "l2"]
    tts_calls = [c for c in services.scheduler.calls if c[1] == "tts.generate"]
    assert len(tts_calls) == 2 * 3  # 首次 + 重做 2 轮
    assert tts_calls[-1][2]["seed"] != tts_calls[2][2].get("seed")


@needs_ffmpeg
async def test_meeting_without_analysis_pack(services, monkeypatch, tmp_path):
    use_fake_llm(monkeypatch, {"title": "周会", "summary": "讨论了进度",
                               "action_items": [{"owner": "小王", "task": "写文档"}]})
    audio = write_wav(tmp_path / "meeting.wav", tone(3.0), 24000)
    task = await run_task(services, "meeting.summarize", {"audio_path": str(audio)})
    assert task["status"] == "succeeded", task["error"]
    result = task["result"]
    assert result["diarized"] is False
    assert result["summary"]["action_items"][0]["owner"] == "小王"
    assert "你好世界" in result["transcript"]


@pytest.mark.parametrize("edit_type", ["emotion"])
async def test_audio_edit_requires_target(services, tmp_path, edit_type):
    audio = write_wav(tmp_path / "a.wav", tone(1.0), 24000)
    params = {"audio_path": str(audio), "edit_type": edit_type}
    task = await run_task(services, "audio.edit", params)
    assert task["status"] == "failed" and "需要填写目标" in task["error"]


async def test_music_without_extension_pack(services):
    task = await run_task(services, "music.generate", {"style": "lofi"})
    assert task["status"] == "failed"
    assert "音乐扩展包未安装" in task["error"]


async def test_worker_errors_are_shown_without_class_name(services):
    from contextlib import asynccontextmanager

    from shengjing_engine.core.workers import WorkerError

    class Broken:
        @asynccontextmanager
        async def use(self, model_id):
            raise WorkerError("speech 进程启动失败（退出码 1）")
            yield

    services.scheduler = Broken()
    task = await run_task(services, "tts.quick", {"text": "你好"})
    assert task["error"] == "speech 进程启动失败（退出码 1）"
