"""混音：把台词、音效、配乐按时间轴叠在一起。

所有片段先统一成同一个采样率和声道数（由调用方用 ffmpeg 解码时完成），
这里只做纯数组运算，方便测试。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Clip:
    samples: np.ndarray
    """[channels, samples]，采样率和时间轴一致。"""
    start: float
    gain_db: float = 0.0
    fade_in: float = 0.005
    fade_out: float = 0.01
    track: str = "voice"


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def to_channels(x: np.ndarray, channels: int) -> np.ndarray:
    if x.ndim == 1:
        x = x[None, :]
    if x.shape[0] == channels:
        return x
    if x.shape[0] == 1:
        return np.repeat(x, channels, axis=0)
    mono = x.mean(axis=0, keepdims=True)
    return np.repeat(mono, channels, axis=0)


def _apply_fades(x: np.ndarray, sr: int, fade_in: float, fade_out: float) -> np.ndarray:
    x = x.copy()
    n = x.shape[1]
    fi = min(n, int(fade_in * sr))
    fo = min(n, int(fade_out * sr))
    if fi > 1:
        x[:, :fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
    if fo > 1:
        x[:, n - fo :] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    return x


def layout_sequential(
    durations: list[float], pauses: list[float], *, start: float = 0.0
) -> list[float]:
    """顺序排列台词：返回每句的开始时间。"""
    if len(durations) != len(pauses):
        raise ValueError("durations 和 pauses 长度不一致")
    starts = []
    t = start
    for dur, pause in zip(durations, pauses, strict=True):
        starts.append(round(t, 4))
        t += dur + pause
    return starts


def fit_length(x: np.ndarray, n: int, sr: int, *, loop: bool, crossfade: float = 0.5) -> np.ndarray:
    """把片段裁剪或循环到 n 个采样点。配乐用循环，音效直接裁剪。"""
    if x.shape[1] >= n:
        return x[:, :n]
    if not loop or x.shape[1] == 0:
        out = np.zeros((x.shape[0], n), dtype=np.float32)
        out[:, : x.shape[1]] = x
        return out
    xf = min(int(crossfade * sr), x.shape[1] // 4)
    out = x.copy()
    while out.shape[1] < n:
        if xf > 0:
            ramp = np.linspace(0.0, 1.0, xf, dtype=np.float32)
            tail = out[:, -xf:] * (1.0 - ramp) + x[:, :xf] * ramp
            out = np.concatenate([out[:, :-xf], tail, x[:, xf:]], axis=1)
        else:
            out = np.concatenate([out, x], axis=1)
    return out[:, :n]


def render(clips: list[Clip], sr: int, channels: int = 2, *, tail: float = 0.5) -> np.ndarray:
    if not clips:
        return np.zeros((channels, 0), dtype=np.float32)
    end = max(c.start + c.samples.shape[1] / sr for c in clips) + tail
    out = np.zeros((channels, int(np.ceil(end * sr))), dtype=np.float32)
    for clip in clips:
        x = to_channels(clip.samples.astype(np.float32), channels)
        x = _apply_fades(x, sr, clip.fade_in, clip.fade_out) * db_to_gain(clip.gain_db)
        s = int(round(clip.start * sr))
        e = min(out.shape[1], s + x.shape[1])
        if e > s:
            out[:, s:e] += x[:, : e - s]
    return out


def envelope_db(x: np.ndarray, sr: int, frame: float = 0.02) -> np.ndarray:
    """每 frame 秒一个 RMS 值（dBFS）。"""
    mono = x.mean(axis=0) if x.ndim == 2 else x
    hop = max(1, int(frame * sr))
    n = int(np.ceil(mono.size / hop))
    padded = np.zeros(n * hop, dtype=np.float32)
    padded[: mono.size] = mono
    rms = np.sqrt(np.mean(padded.reshape(n, hop) ** 2, axis=1) + 1e-12)
    return 20.0 * np.log10(rms + 1e-12)


def duck(
    bgm: np.ndarray,
    voice: np.ndarray,
    sr: int,
    *,
    reduce_db: float = 10.0,
    threshold_db: float = -45.0,
    attack: float = 0.08,
    release: float = 0.6,
    frame: float = 0.02,
) -> np.ndarray:
    """有人说话时自动压低配乐。bgm 和 voice 需等长。"""
    if bgm.shape[1] != voice.shape[1]:
        raise ValueError("bgm 和 voice 长度必须一致")
    env = envelope_db(voice, sr, frame)
    target = np.where(env > threshold_db, db_to_gain(-reduce_db), 1.0).astype(np.float32)
    a = np.exp(-frame / max(attack, 1e-4))
    r = np.exp(-frame / max(release, 1e-4))
    gains = np.empty_like(target)
    g = 1.0
    for i, t in enumerate(target):
        coef = a if t < g else r
        g = coef * g + (1.0 - coef) * t
        gains[i] = g
    hop = max(1, int(frame * sr))
    per_sample = np.repeat(gains, hop)[: bgm.shape[1]]
    if per_sample.size < bgm.shape[1]:
        per_sample = np.pad(per_sample, (0, bgm.shape[1] - per_sample.size), constant_values=g)
    return bgm * per_sample[None, :]


def peak_limit(x: np.ndarray, ceiling_db: float = -1.0) -> np.ndarray:
    """峰值超过上限时整体缩小（避免削波）。响度标准化在导出时由 ffmpeg 完成。"""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    ceiling = db_to_gain(ceiling_db)
    if peak > ceiling:
        return x * (ceiling / peak)
    return x
