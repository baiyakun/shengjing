from __future__ import annotations

import math
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pytest

os.environ.setdefault("SHENGJING_SECRETS", "memory")
os.environ.setdefault("SHENGJING_TOTAL_MEMORY_GB", "32")
# 测试默认按 Apple 芯片处理（在 Intel Mac 的 CI 上也一样）；Intel 的情况单独测试
os.environ.setdefault("SHENGJING_LOCAL_MODELS", "1")

from shengjing_engine.audio.ffmpeg import write_wav  # noqa: E402
from shengjing_engine.config import DataPaths, RuntimePaths  # noqa: E402
from shengjing_engine.core import catalog  # noqa: E402
from shengjing_engine.orchestration.compiler import strip_markup  # noqa: E402
from shengjing_engine.services import EngineServices, create_services  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")


def tone(seconds: float, sr: int = 24000, freq: float = 220.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


class FakeHandle:
    """代替真实模型进程：TTS 写一段正弦波，ASR 返回预设文字。"""

    def __init__(self, spec: catalog.ModelSpec, log: list, transcripts: dict[str, str]):
        self.spec = spec
        self.model_id = spec.id
        self.base_url = "http://127.0.0.1:9"
        self.token = "test-token"
        self._log = log
        self._transcripts = transcripts

    async def run(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        self._log.append((self.model_id, op, params))
        if op == "tts.generate":
            text = params.get("text") or ""
            seconds = max(0.5, min(4.0, len(text) * 0.08))
            write_wav(params["output_path"], tone(seconds), 24000)
            # 真实 ASR 听不到标签，只会转写出实际读出来的文字
            self._transcripts.setdefault(params["output_path"], strip_markup(text))
            return {"path": params["output_path"], "duration": seconds, "sample_rate": 24000}
        if op == "asr.transcribe":
            return {"text": self._transcripts.get(params["audio_path"], "你好世界"),
                    "language": "Chinese"}
        if op == "diarize":
            return {"segments": [
                {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                {"start": 1.2, "end": 2.0, "speaker": "SPEAKER_01"},
            ]}
        raise AssertionError(f"unexpected op {op}")


class FakeScheduler:
    def __init__(self, transcripts: dict[str, str] | None = None) -> None:
        self.calls: list = []
        self.used: list[str] = []
        self.transcripts = transcripts if transcripts is not None else {}

    @asynccontextmanager
    async def use(self, model_id: str):
        self.used.append(model_id)
        yield FakeHandle(catalog.get(model_id), self.calls, self.transcripts)

    def status(self) -> dict[str, Any]:
        return {"budget_gb": 20, "used_gb": 0, "workers": []}


@pytest.fixture
def services(tmp_path: Path) -> EngineServices:
    paths = DataPaths(tmp_path / "data")
    runtime = RuntimePaths(base_python=sys.executable)
    svc = create_services(paths, runtime, token="test-token")
    svc.scheduler = FakeScheduler()  # type: ignore[assignment]
    return svc
