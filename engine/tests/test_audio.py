import numpy as np
import pytest

from shengjing_engine.audio import mix
from shengjing_engine.audio.ffmpeg import FFmpeg, read_wav_mono, wav_duration, write_wav
from shengjing_engine.audio.segment import Span, build_turns, split_on_silence
from shengjing_engine.audio.subtitles import Cue, clock, to_srt

from .conftest import needs_ffmpeg, tone


def test_layout_sequential():
    assert mix.layout_sequential([1.0, 2.0, 0.5], [0.3, 0.2, 0.0], start=0.5) == [0.5, 1.8, 4.0]


def test_render_places_clips_and_applies_gain():
    sr = 1000
    a = np.ones((1, 100), dtype=np.float32)
    out = mix.render([mix.Clip(a, 0.5, gain_db=-6.0, fade_in=0, fade_out=0)], sr, 2, tail=0)
    assert out.shape == (2, 600)
    assert out[0, 499] == 0
    assert out[0, 550] == pytest.approx(10 ** (-6 / 20), rel=1e-4)


def test_fit_length_loops_bgm():
    sr = 1000
    x = np.ones((2, 400), dtype=np.float32)
    y = mix.fit_length(x, 1000, sr, loop=True)
    assert y.shape == (2, 1000)
    assert mix.fit_length(x, 100, sr, loop=False).shape == (2, 100)


def test_duck_lowers_music_under_speech():
    sr = 8000
    bgm = np.ones((2, sr * 2), dtype=np.float32) * 0.5
    voice = np.zeros((2, sr * 2), dtype=np.float32)
    voice[:, sr:] = tone(1.0, sr=sr)[None, :]
    out = mix.duck(bgm, voice, sr, reduce_db=12)
    assert out[0, sr // 2] == pytest.approx(0.5, rel=1e-3)
    assert out[0, -10] < 0.5 * 10 ** (-10 / 20)


def test_peak_limit():
    x = np.array([[2.0, -0.5]], dtype=np.float32)
    assert np.max(np.abs(mix.peak_limit(x, -1.0))) == pytest.approx(10 ** (-1 / 20), rel=1e-5)


def test_split_on_silence_cuts_in_quiet_region():
    sr = 1000
    x = np.concatenate([tone(50, sr, 100), np.zeros(sr * 2, np.float32), tone(30, sr, 100)])
    spans = split_on_silence(x, sr, max_len=60, search=15)
    assert len(spans) == 2
    assert 50.0 <= spans[0].end <= 52.0
    assert spans[-1].end == pytest.approx(82.0)


def test_build_turns_merges_and_splits():
    segs = [Span(0, 1, "A"), Span(1.2, 2, "A"), Span(2.5, 2.6, "B"), Span(3, 130, "B")]
    turns = build_turns(segs, max_len=60)
    assert turns[0] == Span(0, 2, "A")
    assert all(t.speaker == "B" for t in turns[1:])
    assert len(turns) == 4  # B 的 0.1 秒碎片被丢弃，127 秒长段切成 3 段


def test_srt_and_clock():
    srt = to_srt([Cue(0, 1.5, "你好", "旁白"), Cue(61.2, 3725.004, "再见")], with_speaker=True)
    assert "00:00:00,000 --> 00:00:01,500\n旁白：你好" in srt
    assert "00:01:01,200 --> 01:02:05,004\n再见" in srt
    assert clock(75) == "01:15" and clock(3725) == "1:02:05"


def test_wav_roundtrip(tmp_path):
    path = write_wav(tmp_path / "a.wav", tone(0.5), 24000)
    assert wav_duration(path) == pytest.approx(0.5)
    data, sr = read_wav_mono(path)
    assert sr == 24000 and data.size == 12000


@needs_ffmpeg
def test_ffmpeg_decode_encode(tmp_path):
    ff = FFmpeg()
    src = write_wav(tmp_path / "a.wav", tone(1.0), 24000)
    x = ff.decode(src, sample_rate=48000, channels=2)
    assert x.shape[0] == 2 and abs(x.shape[1] - 48000) < 50
    out = ff.encode(x, 48000, tmp_path / "b.mp3", loudness_lufs=-16)
    assert ff.duration(out) == pytest.approx(1.0, abs=0.1)
    wav16 = ff.to_wav(out, tmp_path / "c.wav")
    assert read_wav_mono(wav16)[1] == 16000
