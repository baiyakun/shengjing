"""不依赖 PyTorch 的部分：结果格式转换、命令拼装、错误处理。"""

from types import SimpleNamespace

import pytest
from shengjing_worker_kit import BackendError

from shengjing_analysis_worker.backend import AnalysisBackend, annotation_segments, demucs_command


def turn(s, e):
    return SimpleNamespace(start=s, end=e)


def test_annotation_segments_v4_iteration():
    assert annotation_segments([(turn(0, 1.23456), "SPEAKER_00")]) == [
        {"start": 0.0, "end": 1.235, "speaker": "SPEAKER_00"}
    ]


def test_annotation_segments_legacy_itertracks():
    class Legacy:
        def itertracks(self, yield_label):
            yield turn(1, 2), "A", "SPEAKER_01"

    assert annotation_segments(Legacy())[0]["speaker"] == "SPEAKER_01"


def test_demucs_command():
    cmd = demucs_command("/in.wav", "/out", model="htdemucs", device="mps")
    assert cmd[1:] == ["-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs", "-d", "mps",
                       "-o", "/out", "/in.wav"]


def test_unknown_model_and_unloaded():
    b = AnalysisBackend()
    with pytest.raises(BackendError):
        b.load("nope", "nope", {})
    with pytest.raises(BackendError, match="没有加载"):
        b.run("diarize", "pyannote-community-1", {})
