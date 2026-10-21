"""长音频切段：转写前按静音切开；有说话人区分结果时按发言轮次切开。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mix import envelope_db


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    speaker: str | None = None

    @property
    def length(self) -> float:
        return self.end - self.start


def split_on_silence(
    samples: np.ndarray,
    sr: int,
    *,
    max_len: float = 60.0,
    search: float = 10.0,
    frame: float = 0.02,
) -> list[Span]:
    """把音频切成不超过 max_len 秒的段，切点选在目标位置前 search 秒内最安静的地方。"""
    total = samples.shape[-1] / sr
    if total <= max_len:
        return [Span(0.0, total)] if total > 0 else []
    env = envelope_db(samples, sr, frame)
    spans: list[Span] = []
    cursor = 0.0
    while total - cursor > max_len:
        target = cursor + max_len
        lo = int(max(cursor + 1.0, target - search) / frame)
        hi = int(target / frame)
        window = env[lo:hi]
        cut = (lo + int(np.argmin(window))) * frame if window.size else target
        spans.append(Span(round(cursor, 3), round(cut, 3)))
        cursor = cut
    spans.append(Span(round(cursor, 3), round(total, 3)))
    return spans


def build_turns(
    segments: list[Span],
    *,
    merge_gap: float = 0.8,
    max_len: float = 60.0,
    min_len: float = 0.4,
) -> list[Span]:
    """把说话人区分的碎片段合并成发言轮次。

    - 同一说话人、间隔小于 merge_gap 的相邻段合并
    - 超过 max_len 的轮次按 max_len 均匀切开
    - 短于 min_len 的碎片丢弃（转写这么短的片段基本都是噪声）
    """
    ordered = sorted(segments, key=lambda s: s.start)
    merged: list[Span] = []
    for seg in ordered:
        if merged and merged[-1].speaker == seg.speaker and seg.start - merged[-1].end < merge_gap:
            last = merged[-1]
            merged[-1] = Span(last.start, max(last.end, seg.end), last.speaker)
        else:
            merged.append(seg)
    turns: list[Span] = []
    for turn in merged:
        if turn.length < min_len:
            continue
        if turn.length <= max_len:
            turns.append(turn)
            continue
        pieces = int(np.ceil(turn.length / max_len))
        step = turn.length / pieces
        for i in range(pieces):
            turns.append(
                Span(
                    round(turn.start + i * step, 3),
                    round(turn.start + (i + 1) * step, 3),
                    turn.speaker,
                )
            )
    return turns
