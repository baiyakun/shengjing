"""在线服务商：用模拟的 HTTP 服务检查请求格式和结果解析（不会真的调用在线服务）。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import msgpack
import pytest

from shengjing_engine.audio.ffmpeg import FFmpeg, wav_duration, write_wav
from shengjing_engine.core.db import CloudVoiceStore, Database
from shengjing_engine.providers import PROVIDERS, ProviderContext, ProviderError
from shengjing_engine.providers.base import words_to_segments

from .conftest import needs_ffmpeg, tone


def wav_bytes(seconds: float = 0.5, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    import wave

    import numpy as np

    pcm = (tone(seconds, sr) * 32767).astype(np.int16)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def mp3_bytes(tmp_path: Path) -> bytes:
    src = write_wav(tmp_path / "src.wav", tone(0.5), 24000)
    return FFmpeg().to_mp3(src, tmp_path / "src.mp3").read_bytes()


class Server:
    """记录请求，按路径返回预设响应。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = f"{request.method} {request.url.path}"
        handler = self.routes.get(key) or self.routes.get(f"{request.method} {request.url.host}")
        if handler is None:
            return httpx.Response(404, json={"message": f"no route {key}"})
        return handler(request) if callable(handler) else handler

    def paths(self):
        return [f"{r.method} {r.url.path}" for r in self.calls]


def provider(pid: str, tmp_path: Path, server: Server, *, key="k", options=None, db=None):
    db = db or Database(tmp_path / "db.sqlite3")
    ctx = ProviderContext(
        api_key=key,
        base_url="",
        options=options or {},
        ffmpeg=FFmpeg(),
        voices=CloudVoiceStore(db),
        tmp_dir=tmp_path / "tmp",
        transport=httpx.MockTransport(server),
        poll_interval=0,
    )
    return PROVIDERS[pid](ctx)


def ref_wav(tmp_path: Path) -> str:
    return str(write_wav(tmp_path / "ref.wav", tone(2.0), 24000))


# ---------------- OpenAI 兼容 ----------------


async def test_openai_tts_and_asr(tmp_path):
    def speech(req):
        body = json.loads(req.content)
        assert req.headers["Authorization"] == "Bearer k"
        assert body == {"model": "gpt-4o-mini-tts", "input": "你好", "voice": "coral",
                        "response_format": "wav", "instructions": "温柔"}
        return httpx.Response(200, content=wav_bytes())

    def transcribe(req):
        assert b'name="model"' in req.content and b"gpt-4o-transcribe" in req.content
        return httpx.Response(200, json={"text": "你好"})

    server = Server({"POST /v1/audio/speech": speech,
                     "POST /v1/audio/transcriptions": transcribe})
    p = provider("openai", tmp_path, server, options={"tts_voice": "coral"})
    out = await p.run("tts", "tts.generate", {"text": "你好", "instructions": "温柔",
                                               "output_path": str(tmp_path / "a.wav")})
    assert out["duration"] == pytest.approx(0.5)
    res = await p.run("asr", "asr.transcribe", {"audio_path": out["path"]})
    assert res["text"] == "你好"


async def test_missing_key_and_http_errors_are_readable(tmp_path):
    p = provider("openai", tmp_path, Server({}), key=None)
    with pytest.raises(ProviderError, match="API Key"):
        await p.run("tts", "tts.generate", {"text": "x", "output_path": str(tmp_path / "a.wav")})
    server = Server({"POST /v1/audio/speech": httpx.Response(401, json={"error": "bad key"})})
    p = provider("openai", tmp_path, server)
    with pytest.raises(ProviderError, match="检查 API Key"):
        await p.run("tts", "tts.generate", {"text": "x", "output_path": str(tmp_path / "a.wav")})


# ---------------- SenseAudio ----------------


@needs_ffmpeg
async def test_senseaudio_tts_clones_once(tmp_path):
    def upload(req):
        assert b'name="purpose"' in req.content and b"voice_clone" in req.content
        return httpx.Response(200, json={"file": {"file_id": "file-1"},
                                         "base_resp": {"status_code": 0}})

    def clone(req):
        body = json.loads(req.content)
        assert body["file_id"] == "file-1" and body["label"].startswith("sj_")
        return httpx.Response(200, json={"label": body["label"]})

    def t2a(req):
        body = json.loads(req.content)
        assert body["voice_setting"]["voice_id"].startswith("sj_")
        assert body["audio_setting"]["format"] == "wav" and body["stream"] is False
        return httpx.Response(200, json={"data": {"audio": wav_bytes().hex(), "status": 2},
                                         "base_resp": {"status_code": 0}})

    server = Server({"POST /v1/files/upload": upload, "POST /v1/voice/clone": clone,
                     "POST /v1/t2a_v2": t2a})
    p = provider("senseaudio", tmp_path, server)
    ref = ref_wav(tmp_path)
    for i in range(2):
        out = await p.run("tts", "tts.generate", {
            "text": "你好", "reference_audio": ref, "reference_text": "参考",
            "output_path": str(tmp_path / f"{i}.wav")})
        assert wav_duration(out["path"]) == pytest.approx(0.5)
    assert server.paths().count("POST /v1/voice/clone") == 1  # 第二次用缓存


async def test_senseaudio_asr_diarization_and_big_file_upload(tmp_path):
    def transcribe(req):
        content = req.content
        assert b"verbose_json" in content and b"enable_speaker_diarization" in content
        return httpx.Response(200, json={"text": "你好 再见", "segments": [
            {"start": 0, "end": 1, "text": "你好", "speaker": "speaker_0"},
            {"start": 1, "end": 2, "text": "再见", "speaker": "speaker_1"}]})

    uploaded = {"file": {"file_id": "big-1"}, "base_resp": {"status_code": 0}}
    server = Server({"POST /v1/audio/transcriptions": transcribe,
                     "POST /v1/files/upload": httpx.Response(200, json=uploaded)})
    p = provider("senseaudio", tmp_path, server)
    small = tmp_path / "s.wav"
    small.write_bytes(wav_bytes())
    res = await p.run("asr", "asr.transcribe", {"audio_path": str(small), "diarize": True})
    assert [s["speaker"] for s in res["segments"]] == ["speaker_0", "speaker_1"]

    big = tmp_path / "big.wav"
    big.write_bytes(b"0" * (10 * 1024 * 1024 + 1))
    await p.run("asr", "asr.transcribe", {"audio_path": str(big), "diarize": True})
    last = server.calls[-1]
    assert b'name="file_id"' in last.content and b"big-1" in last.content
    assert b'filename=' not in last.content  # 没有再直接上传大文件


async def test_senseaudio_sfx_music_and_audiogen(tmp_path):
    polls = iter([{"status": "PENDING"}, {"status": "SUCCESS", "response": {"data": [
        {"title": "海边", "lyrics": "la", "audio_url": "https://cdn.example/m.wav",
         "derived_audio": {"wav": "https://cdn.example/m.wav"}}]}}])

    def sfx(req):
        body = json.loads(req.content)
        assert body["duration_seconds"] == 10 and body["variants_count"] == 1  # 30 秒被限制到 10
        return httpx.Response(200, json={"status": "completed", "items": [
            {"status": "completed", "audio_url": "https://cdn.example/s.wav",
             "output_format": "wav"}]})

    def generate(req):
        body = json.loads(req.content)
        assert body["model"] == "senseaudio-a1"
        assert body["references"][0]["audio_url"].startswith("data:audio/mpeg;base64,")
        return httpx.Response(200, json={"audio_url": "https://cdn.example/g.wav",
                                         "subtitle": {"text": "x"}})

    server = Server({
        "POST /v1/sound-effects/generations": sfx,
        "POST /v2/music/song/create": httpx.Response(200, json={"task_id": "t1"}),
        "GET /v1/music/song/pending/t1": lambda req: httpx.Response(200, json=next(polls)),
        "POST /v1/audio/generate": generate,
        "GET cdn.example": lambda req: httpx.Response(200, content=wav_bytes()),
    })
    p = provider("senseaudio", tmp_path, server)
    out = await p.run("sfx", "tts.generate", {"text": "雷声", "duration_seconds": 30,
                                               "output_path": str(tmp_path / "s.wav")})
    assert out["duration"] > 0
    music = await p.run("music", "music.generate", {"prompt": "海边", "style": "民谣",
                                                     "lyrics": "[verse]\nla",
                                                     "output_dir": str(tmp_path / "m")})
    assert music["title"] == "海边" and Path(music["files"][0]).exists()
    if FFmpeg().available:
        gen = await p.run("dialogue", "tts.generate", {
            "text": "角色列表：…", "references": [ref_wav(tmp_path)],
            "output_path": str(tmp_path / "g.wav")})
        assert gen["subtitle"] == {"text": "x"}
    # 下载音频时不带 API Key
    downloads = [c for c in server.calls if c.url.host == "cdn.example"]
    assert downloads and all("Authorization" not in c.headers for c in downloads)


# ---------------- Fish Audio ----------------


@needs_ffmpeg
async def test_fish_audio_inline_references_use_msgpack(tmp_path):
    seen = {}

    def tts(req):
        seen["model"] = req.headers["model"]
        if req.headers["content-type"] == "application/msgpack":
            body = msgpack.unpackb(req.content, raw=False)
            seen["refs"] = body["references"]
        else:
            seen["json"] = json.loads(req.content)
        return httpx.Response(200, content=wav_bytes())

    server = Server({"POST /v1/tts": tts})
    p = provider("fish_audio", tmp_path, server, options={"tts_model": "drama-3"})
    await p.run("tts", "tts.generate", {"text": "[whisper] 你好", "reference_audio":
                                        ref_wav(tmp_path), "reference_text": "参考",
                                        "output_path": str(tmp_path / "a.wav")})
    assert seen["model"] == "drama-3"
    assert seen["refs"][0]["text"] == "参考" and isinstance(seen["refs"][0]["audio"], bytes)
    await p.run("tts", "tts.generate", {"text": "你好", "voice": "abc",
                                        "output_path": str(tmp_path / "b.wav")})
    assert seen["json"]["reference_id"] == "abc"
    with pytest.raises(ProviderError, match="文字稿"):
        await p.run("tts", "tts.generate", {"text": "x", "reference_audio": "/r.wav",
                                            "output_path": str(tmp_path / "c.wav")})


# ---------------- ElevenLabs ----------------


@needs_ffmpeg
async def test_elevenlabs_clone_tts_sfx_music(tmp_path):
    mp3 = mp3_bytes(tmp_path)

    def add_voice(req):
        assert req.headers["xi-api-key"] == "k"
        return httpx.Response(200, json={"voice_id": "v123", "requires_verification": False})

    def tts(req):
        assert req.url.path == "/v1/text-to-speech/v123"
        assert req.url.params["output_format"] == "mp3_44100_128"
        assert json.loads(req.content)["model_id"] == "eleven_v3"
        return httpx.Response(200, content=mp3)

    server = Server({
        "POST /v1/voices/add": add_voice,
        "POST /v1/text-to-speech/v123": tts,
        "POST /v1/sound-generation": lambda req: httpx.Response(200, content=mp3),
        "POST /v1/music": lambda req: httpx.Response(200, content=mp3),
    })
    p = provider("elevenlabs", tmp_path, server)
    ref = ref_wav(tmp_path)
    for i in range(2):
        out = await p.run("tts", "tts.generate", {"text": "[sighs] hi", "reference_audio": ref,
                                                   "output_path": str(tmp_path / f"{i}.wav")})
    assert server.paths().count("POST /v1/voices/add") == 1
    assert out["duration"] > 0.3  # mp3 已转成 wav
    await p.run("sfx", "tts.generate", {"text": "door", "duration_seconds": 100,
                                         "output_path": str(tmp_path / "s.wav")})
    sfx_body = json.loads(server.calls[-1].content)
    assert sfx_body["duration_seconds"] == 30.0
    music = await p.run("music", "music.generate", {"style": "lofi", "duration": 20,
                                                     "output_dir": str(tmp_path / "m")})
    body = json.loads(server.calls[-1].content)
    assert body["force_instrumental"] is True and body["music_length_ms"] == 20000
    assert Path(music["files"][0]).exists()


async def test_elevenlabs_asr_groups_words_by_speaker(tmp_path):
    words = [
        {"text": "你好", "type": "word", "start": 0.0, "end": 0.4, "speaker_id": "speaker_0"},
        {"text": "，", "type": "spacing", "start": 0.4, "end": 0.4, "speaker_id": "speaker_0"},
        {"text": "我是小王", "type": "word", "start": 0.5, "end": 1.2, "speaker_id": "speaker_0"},
        {"text": "欢迎", "type": "word", "start": 1.5, "end": 2.0, "speaker_id": "speaker_1"},
    ]
    server = Server({"POST /v1/speech-to-text": httpx.Response(
        200, json={"text": "你好，我是小王欢迎", "words": words})})
    p = provider("elevenlabs", tmp_path, server)
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    res = await p.run("asr", "asr.transcribe", {"audio_path": str(f), "diarize": True})
    assert [(s["speaker"], s["text"]) for s in res["segments"]] == [
        ("speaker_0", "你好，我是小王"), ("speaker_1", "欢迎")]
    assert b'name="diarize"' in server.calls[-1].content


def test_words_to_segments_splits_long_pauses():
    words = [{"text": "a", "start": 0, "end": 1, "speaker_id": "s"},
             {"text": "b", "start": 5, "end": 6, "speaker_id": "s"}]
    assert len(words_to_segments(words, max_gap=1.0)) == 2


# ---------------- 通过引擎调用 ----------------


@needs_ffmpeg
async def test_engine_routes_cloud_models_through_scheduler(tmp_path):
    import sys

    from shengjing_engine.config import DataPaths, RuntimePaths
    from shengjing_engine.services import create_services

    from .test_workflows import run_task

    services = create_services(DataPaths(tmp_path / "data"), RuntimePaths(sys.executable), "t")
    server = Server({"POST /v1/audio/speech": lambda req: httpx.Response(200, content=wav_bytes())})
    original = services.provider_context
    services.provider_context = lambda pid, **kw: original(
        pid, transport=httpx.MockTransport(server), **kw)

    failed = await run_task(services, "tts.quick", {"text": "你好", "model": "openai-tts"})
    assert failed["status"] == "failed"
    assert failed["error"] == "请先在设置里填写 OpenAI 兼容接口 的 API Key"

    services.secrets.set("openai_api_key", "k")
    services.settings.update({"model_overrides": {"tts": "openai-tts"}})
    task = await run_task(services, "tts.quick", {"text": "你好", "delivery": "开心"})
    assert task["status"] == "succeeded", task["error"]
    assert json.loads(server.calls[-1].content)["instructions"] == "开心"
    assert services.scheduler.planner.used_gb() == 0  # 在线 API 不占本机内存


@needs_ffmpeg
async def test_meeting_uses_cloud_diarization(tmp_path, monkeypatch):
    import sys

    from shengjing_engine.config import DataPaths, RuntimePaths
    from shengjing_engine.services import create_services

    from .test_workflows import run_task, use_fake_llm

    services = create_services(DataPaths(tmp_path / "data"), RuntimePaths(sys.executable), "t")
    words = [{"text": "开会了", "type": "word", "start": 0.0, "end": 1.0, "speaker_id": "A"},
             {"text": "好的", "type": "word", "start": 1.5, "end": 2.0, "speaker_id": "B"}]
    server = Server({"POST /v1/speech-to-text": httpx.Response(
        200, json={"text": "开会了好的", "words": words})})
    original = services.provider_context
    services.provider_context = lambda pid, **kw: original(
        pid, transport=httpx.MockTransport(server), **kw)
    services.secrets.set("elevenlabs_api_key", "k")
    use_fake_llm(monkeypatch, {"title": "周会", "summary": "开始开会"})
    audio = write_wav(tmp_path / "m.wav", tone(2.0), 24000)
    task = await run_task(services, "meeting.summarize",
                          {"audio_path": str(audio), "model": "elevenlabs-asr"})
    assert task["status"] == "succeeded", task["error"]
    assert task["result"]["diarized"] is True
    assert "A：开会了" in task["result"]["transcript"]
    assert "B：好的" in task["result"]["transcript"]
    sent = server.calls[-1].content
    assert b"upload.mp3" in sent  # 上传的是压缩后的 mp3


def test_provider_settings_api(services):
    from fastapi.testclient import TestClient

    from shengjing_engine.api.app import create_app

    auth = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(services, run_background=False)) as c:
        providers = {p["id"]: p for p in c.get("/providers", headers=auth).json()}
        assert set(providers) == {"openai", "senseaudio", "fish_audio", "elevenlabs"}
        assert providers["openai"]["ready"] is False
        r = c.put("/providers/openai", json={
            "base_url": "https://api.siliconflow.cn/v1",
            "options": {"tts_voice": "nova", "asr_model": ""}, "api_key": "sk-x"}, headers=auth)
        openai = next(p for p in r.json() if p["id"] == "openai")
        assert openai["ready"] and openai["base_url"] == "https://api.siliconflow.cn/v1"
        assert openai["values"] == {"tts_voice": "nova"}  # 空值不保存，使用默认
        # SenseAudio 的接口地址不允许修改
        r = c.put("/providers/senseaudio", json={"base_url": "https://evil.example"},
                  headers=auth)
        assert next(p for p in r.json() if p["id"] == "senseaudio")["base_url"] == ""
        models = {m["id"]: m for m in c.get("/catalog", headers=auth).json()}
        assert models["openai-tts"]["worker_installed"] is True
        assert models["elevenlabs-tts"]["worker_installed"] is False
        assert c.put("/providers/nope", json={}, headers=auth).status_code == 404
