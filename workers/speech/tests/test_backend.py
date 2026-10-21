"""用一个假的 mlx_speech 模块测试适配逻辑，Linux 上也能跑。"""

from __future__ import annotations

import base64
import sys
import types
import wave
from dataclasses import dataclass

import numpy as np
import pytest
from shengjing_worker_kit import BackendError


@dataclass
class Out:
    waveform: np.ndarray
    sample_rate: int


class FakeTTS:
    def __init__(self, source):
        self.source = source
        self.calls = []
        self.prepared = 0

    def prepare_reference(self, path, *, reference_text):
        self.prepared += 1
        return ("prepared", path, reference_text)

    def generate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return Out(np.zeros(2400, dtype=np.float32), 24000)


class FakeStereo:
    """不支持预编码参考录音的模型（例如 DramaBox），输出立体声。"""

    def __init__(self, source):
        self.source = source
        self.calls = []

    def generate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return Out(np.zeros((2, 4800), dtype=np.float32), 48000)


@dataclass
class Update:
    delta: str
    committed: str
    hypothesis: str


class FakeASR:
    def __init__(self, source):
        self.source = source

    def generate(self, audio, **kwargs):
        return types.SimpleNamespace(text=f"heard:{audio}:{kwargs.get('language')}",
                                     language="Chinese")

    def stream_session(self, **kwargs):
        class S:
            def feed(self, pcm):
                return Update("a", f"{pcm.size}", "b")

            def finalize(self):
                return types.SimpleNamespace(text="final", language="Chinese")

        return S()


def write_wav(path, samples, *, sample_rate):
    arr = np.asarray(samples, dtype=np.float32)
    channels = 1 if arr.ndim == 1 else arr.shape[0]
    data = (arr.T.reshape(-1) * 32767).astype("<i2") if arr.ndim == 2 else (arr * 32767).astype(
        "<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data.tobytes())


@pytest.fixture(autouse=True)
def fake_mlx_speech(monkeypatch):
    mod = types.ModuleType("mlx_speech")
    mod.tts = types.SimpleNamespace(
        load=lambda source, **kw: FakeStereo(source) if source == "dramabox" else FakeTTS(source)
    )
    mod.asr = types.SimpleNamespace(load=lambda source, **kw: FakeASR(source))
    audio = types.ModuleType("mlx_speech.audio")
    audio.write_wav = write_wav
    monkeypatch.setitem(sys.modules, "mlx_speech", mod)
    monkeypatch.setitem(sys.modules, "mlx_speech.audio", audio)
    yield


def backend():
    from shengjing_speech_worker.backend import SpeechBackend

    return SpeechBackend()


def test_tts_reuses_prepared_reference(tmp_path):
    b = backend()
    b.load("fish-s2-pro", "fish-s2-pro", {"kind": "tts"})
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    for i in range(3):
        r = b.run("tts.generate", "fish-s2-pro", {
            "text": "你好", "output_path": str(tmp_path / f"{i}.wav"),
            "reference_audio": str(ref), "reference_text": "参考", "duration_seconds": None,
        })
    assert r["duration"] == pytest.approx(0.1) and r["channels"] == 1
    model = b._models["fish-s2-pro"]
    assert model.prepared == 1
    text, kwargs = model.calls[-1]
    assert text == "你好" and kwargs == {"reference_audio": ("prepared", str(ref), "参考")}


def test_stereo_output_and_plain_reference(tmp_path):
    b = backend()
    b.load("dramabox", "dramabox", {"kind": "tts"})
    r = b.run("tts.generate", "dramabox", {
        "text": "hi", "output_path": str(tmp_path / "o.wav"),
        "reference_audio": "/r.wav", "reference_text": "t",
    })
    assert r["channels"] == 2 and r["sample_rate"] == 48000
    assert b._models["dramabox"].calls[0][1]["reference_text"] == "t"


def test_asr_and_stream(tmp_path):
    b = backend()
    b.load("qwen3-asr-1.7b", "qwen3-asr-1.7b", {"kind": "asr"})
    r = b.run("asr.transcribe", "qwen3-asr-1.7b", {"audio_path": "/a.wav", "language": "Chinese"})
    assert r["text"] == "heard:/a.wav:Chinese"
    sid = b.run("asr.stream_open", "qwen3-asr-1.7b", {})["session_id"]
    pcm = base64.b64encode(np.zeros(1600, dtype="<f4").tobytes()).decode()
    assert b.run("asr.stream_feed", "qwen3-asr-1.7b", {"session_id": sid,
                                                       "pcm_f32_b64": pcm})["committed"] == "1600"
    assert b.run("asr.stream_close", "qwen3-asr-1.7b", {"session_id": sid})["text"] == "final"
    with pytest.raises(BackendError):
        b.run("asr.stream_feed", "qwen3-asr-1.7b", {"session_id": sid})


def test_errors_for_wrong_model_kind_and_unloaded():
    b = backend()
    with pytest.raises(BackendError, match="没有加载"):
        b.run("tts.generate", "x", {})
    b.load("qwen3-asr-1.7b", "qwen3-asr-1.7b", {"kind": "asr"})
    with pytest.raises(BackendError, match="不支持"):
        b.run("tts.generate", "qwen3-asr-1.7b", {"output_path": "/tmp/x.wav"})
    b.unload("qwen3-asr-1.7b")
    assert b.loaded() == []
