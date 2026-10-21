"""字幕（SRT）生成。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str
    speaker: str | None = None


def _ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue], *, with_speaker: bool = False) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        text = cue.text.strip()
        if with_speaker and cue.speaker:
            text = f"{cue.speaker}：{text}"
        blocks.append(f"{i}\n{_ts(cue.start)} --> {_ts(cue.end)}\n{text}\n")
    return "\n".join(blocks)


def clock(seconds: float) -> str:
    """转写稿里用的 mm:ss 时间。"""
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
