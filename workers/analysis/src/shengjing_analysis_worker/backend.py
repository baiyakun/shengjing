"""分析模型适配（全部在第 0 阶段需要在 Mac 上验证）。

- pyannote/speaker-diarization-community-1：离线说话人区分（pyannote.audio 4.x）
- Qwen/Qwen3-ForcedAligner-0.6B：词级时间戳（qwen-asr 包），单段最长约 5 分钟
- HT-Demucs：人声和伴奏分离（demucs 命令行）
- Silero VAD：检测哪里有人声

输入音频统一由主引擎转成 16 kHz 单声道 WAV 后传入。
"""

from __future__ import annotations

import gc
import subprocess
import sys
from pathlib import Path
from typing import Any

from shengjing_worker_kit import BackendError

MAX_ALIGN_SECONDS = 280.0


def torch_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def read_audio(path: str):
    """读成 pyannote 接受的 {"waveform": [1, T] 张量, "sample_rate": sr}。"""
    import soundfile as sf
    import torch

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    waveform = torch.from_numpy(data.mean(axis=1)).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": int(sr)}


def annotation_segments(annotation: Any) -> list[dict[str, Any]]:
    """兼容 pyannote 4.x（直接迭代 (turn, speaker)）和旧版（itertracks）。"""
    items = []
    if hasattr(annotation, "itertracks"):
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            items.append((turn, speaker))
    else:
        items = list(annotation)
    return [
        {"start": round(float(turn.start), 3), "end": round(float(turn.end), 3),
         "speaker": str(speaker)}
        for turn, speaker in items
    ]


def demucs_command(src: str, out_dir: str, *, model: str, device: str) -> list[str]:
    return [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-n", model,
        "-d", device,
        "-o", out_dir,
        src,
    ]


class AnalysisBackend:
    name = "analysis"

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._sources: dict[str, str] = {}

    def load(self, model: str, source: str, options: dict[str, Any]) -> None:
        if model in self._models:
            return
        if model == "pyannote-community-1":
            # 管道需要访问令牌，第一次 diarize 时再加载
            self._models[model] = None
        elif model == "qwen3-forced-aligner":
            import torch
            from qwen_asr import Qwen3ForcedAligner

            device = torch_device()
            dtype = torch.float32 if device == "cpu" else torch.bfloat16
            self._models[model] = Qwen3ForcedAligner.from_pretrained(
                source, dtype=dtype, device_map=device
            )
        elif model == "htdemucs":
            self._models[model] = source  # 每次调用 demucs 命令行
        elif model == "silero-vad":
            from silero_vad import load_silero_vad

            self._models[model] = load_silero_vad()
        else:
            raise BackendError(f"分析进程不认识模型：{model}")
        self._sources[model] = source

    def unload(self, model: str) -> None:
        self._models.pop(model, None)
        self._sources.pop(model, None)
        gc.collect()
        try:
            import torch

            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except ImportError:
            pass

    def loaded(self) -> list[str]:
        return sorted(self._models)

    def memory(self) -> dict[str, Any]:
        try:
            import torch

            if torch.backends.mps.is_available():
                return {"mps_allocated_gb": round(torch.mps.current_allocated_memory() / 1024**3,
                                                  3)}
        except (ImportError, AttributeError):
            pass
        return {}

    def run(self, op: str, model: str, params: dict[str, Any]) -> dict[str, Any]:
        if model not in self._models:
            raise BackendError(f"模型 {model} 没有加载")
        if op == "diarize":
            return self._diarize(params)
        if op == "align":
            return self._align(params)
        if op == "separate":
            return self._separate(params)
        if op == "vad":
            return self._vad(params)
        raise BackendError(f"不支持的操作：{op}")

    def _diarize(self, params: dict[str, Any]) -> dict[str, Any]:
        pipeline = self._models.get("pyannote-community-1")
        if pipeline is None:
            import torch
            from pyannote.audio import Pipeline

            token = params.get("hf_token")
            source = self._sources["pyannote-community-1"]
            try:
                pipeline = Pipeline.from_pretrained(source, token=token)
            except Exception as exc:  # noqa: BLE001
                raise BackendError(
                    "无法加载说话人区分模型：请在 Hugging Face 页面同意使用条款，"
                    f"并在设置里填写访问令牌（{exc}）"
                ) from exc
            pipeline.to(torch.device(torch_device()))
            self._models["pyannote-community-1"] = pipeline
        options = {k: params[k] for k in ("num_speakers", "min_speakers", "max_speakers")
                   if params.get(k)}
        output = pipeline(read_audio(params["audio_path"]), **options)
        # 4.x 提供不重叠的版本，方便和转写对齐
        annotation = getattr(output, "exclusive_speaker_diarization", None) or getattr(
            output, "speaker_diarization", output
        )
        return {"segments": annotation_segments(annotation)}

    def _align(self, params: dict[str, Any]) -> dict[str, Any]:
        import soundfile as sf

        info = sf.info(params["audio_path"])
        if info.duration > MAX_ALIGN_SECONDS:
            raise BackendError(f"时间戳对齐单段最长 {MAX_ALIGN_SECONDS:.0f} 秒，请先切段")
        aligner = self._models["qwen3-forced-aligner"]
        results = aligner.align(
            audio=params["audio_path"],
            text=params["text"],
            language=params.get("language") or "Chinese",
        )
        units = results[0] if results else []
        return {"units": [
            {"text": u.text, "start": float(u.start_time), "end": float(u.end_time)}
            for u in units
        ]}

    def _separate(self, params: dict[str, Any]) -> dict[str, Any]:
        src = params["audio_path"]
        out_dir = Path(params["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        model = self._sources.get("htdemucs", "htdemucs")
        cmd = demucs_command(src, str(out_dir), model=model, device=torch_device())
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise BackendError(f"人声分离失败：{proc.stderr.strip()[-500:]}")
        stem_dir = out_dir / model / Path(src).stem
        vocals, rest = stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"
        if not vocals.exists():
            raise BackendError("人声分离没有产生输出文件")
        return {"vocals": str(vocals), "accompaniment": str(rest)}

    def _vad(self, params: dict[str, Any]) -> dict[str, Any]:
        from silero_vad import get_speech_timestamps
        from silero_vad import read_audio as silero_read

        model = self._models["silero-vad"]
        wav = silero_read(params["audio_path"], sampling_rate=16000)
        stamps = get_speech_timestamps(wav, model, sampling_rate=16000, return_seconds=True)
        return {"segments": [{"start": float(s["start"]), "end": float(s["end"])} for s in stamps]}
