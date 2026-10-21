"""质检回听：把生成的音频转写回文字，和原稿对比。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return _PUNCT_RE.sub(" ", text).strip()


def _tokens(text: str) -> list[str]:
    """中文按字切，英文按词切。"""
    norm = normalize_text(text)
    out: list[str] = []
    for chunk in norm.split():
        if _CJK_RE.search(chunk):
            buf = ""
            for ch in chunk:
                if _CJK_RE.match(ch):
                    if buf:
                        out.append(buf)
                        buf = ""
                    out.append(ch)
                else:
                    buf += ch
            if buf:
                out.append(buf)
        else:
            out.append(chunk)
    return out


def edit_distance(a: list[str], b: list[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str) -> float:
    """中文字错误率 / 英文词错误率（混排时按 token 统一计算）。"""
    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


@dataclass
class QAResult:
    passed: bool
    error_rate: float
    duration: float
    issues: list[str] = field(default_factory=list)
    transcript: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_line(
    expected: str,
    transcript: str,
    duration: float,
    *,
    target_duration: float | None = None,
    max_error_rate: float = 0.12,
    duration_tolerance: float = 0.15,
) -> QAResult:
    issues: list[str] = []
    rate = error_rate(expected, transcript)
    if rate > max_error_rate:
        issues.append(f"转写和原稿差异 {rate:.0%}，可能有吞字、错字或多读")
    if duration < 0.2:
        issues.append("音频几乎是空的")
    if target_duration is not None and target_duration > 0:
        diff = abs(duration - target_duration) / target_duration
        if diff > duration_tolerance:
            issues.append(f"时长 {duration:.2f} 秒，和目标 {target_duration:.2f} 秒相差 {diff:.0%}")
    return QAResult(
        passed=not issues,
        error_rate=round(rate, 4),
        duration=round(duration, 3),
        issues=issues,
        transcript=transcript,
    )
