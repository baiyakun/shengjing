"""工作流共用的小工具。"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..audio.ffmpeg import read_wav_mono, write_wav
from ..audio.segment import Span, split_on_silence
from ..core import catalog
from ..core.tasks import TaskContext, UserFacingError
from ..orchestration.compiler import RenderJob, VoiceRef
from ..orchestration.llm import ChatClient, OpenAICompatClient
from ..services import EngineServices


@contextlib.asynccontextmanager
async def llm_session(services: EngineServices) -> AsyncIterator[ChatClient]:
    """按设置使用本地 LLM（mlx-lm 进程）或云端 LLM。"""
    settings = services.settings.get()
    if settings.llm.provider == "cloud":
        yield services.chat_client_for_cloud()
        return
    model_id = services.llm_model_id()
    async with services.scheduler.use(model_id) as handle:
        yield OpenAICompatClient(f"{handle.base_url}/v1", model=handle.spec.source)


def voice_ref(services: EngineServices, voice_id: str | None) -> VoiceRef | None:
    if not voice_id:
        return None
    voice = services.voices.get(voice_id)
    if voice is None:
        raise UserFacingError(f"音色不存在：{voice_id}")
    return VoiceRef(audio_path=voice["audio_path"], transcript=voice.get("transcript", ""))


def job_params(job: RenderJob, out_path: Path, extra: dict[str, Any] | None = None) -> dict:
    return {"text": job.text, "output_path": str(out_path), **job.params, **(extra or {})}


def require_file(path: str | None, what: str = "文件") -> Path:
    if not path:
        raise UserFacingError(f"请选择{what}")
    p = Path(path).expanduser()
    if not p.is_file():
        raise UserFacingError(f"{what}不存在：{p}")
    return p


async def transcribe_spans(
    ctx: TaskContext,
    wav16k: Path,
    spans: list[Span],
    *,
    model_id: str | None = None,
    language: str | None = None,
    progress_range: tuple[float, float] = (0.0, 1.0),
) -> list[dict[str, Any]]:
    """逐段转写。spans 为空时整段转写。"""
    services = ctx.services
    spec = services.model_for("asr", model_id)
    lang = asr_language(spec, language)
    samples, sr = read_wav_mono(wav16k)
    if not spans:
        spans = split_on_silence(samples, sr)
    chunk_dir = ctx.out_dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    out: list[dict[str, Any]] = []
    lo, hi = progress_range
    async with services.scheduler.use(spec.id) as handle:
        for i, span in enumerate(spans):
            ctx.check_cancelled()
            piece = samples[int(span.start * sr) : int(span.end * sr)]
            if piece.size < sr * 0.2:
                continue
            path = chunk_dir / f"{i:05d}.wav"
            write_wav(path, piece, sr)
            result = await handle.run("asr.transcribe", {"audio_path": str(path), "language": lang})
            text = (result.get("text") or "").strip()
            if text:
                out.append(
                    {
                        "start": span.start,
                        "end": span.end,
                        "speaker": span.speaker,
                        "text": text,
                    }
                )
            ctx.progress(lo + (hi - lo) * (i + 1) / len(spans), f"转写 {i + 1}/{len(spans)}")
    return out


_LANG_NAMES = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
               "fr": "French", "de": "German", "es": "Spanish", "yue": "Cantonese"}
_LOCALES = {"zh": "zh-CN", "en": "en-US", "ja": "ja-JP", "ko": "ko-KR", "fr": "fr-FR",
            "de": "de-DE", "es": "es-ES"}


def asr_language(spec: catalog.ModelSpec, code: str | None) -> str | None:
    """各 ASR 模型要求的语言写法不同（见 mlx-speech 文档）：

    - Cohere：语言代码，例如 zh、en
    - Nemotron：地区代码，例如 zh-CN、en-US
    - Qwen3-ASR、Confucius4、Granite：英文语言名，例如 Chinese
    None 表示让模型自动判断。
    """
    if not code:
        return None
    if spec.id == "cohere-asr":
        return code
    if spec.id.startswith("nemotron"):
        return _LOCALES.get(code, code)
    return _LANG_NAMES.get(code, code)
