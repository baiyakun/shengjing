"""语音转文字与会议纪要。

流程：转成 16 kHz 单声道 → （装了分析包时）说话人区分 → 按发言轮次逐段转写
→ 生成带说话人和时间的转写稿 → LLM 写纪要 → 导出 txt / srt / json。
没有分析包时按静音切段转写，转写稿里不区分说话人。
"""

from __future__ import annotations

import json
from typing import Any

from ..audio.segment import Span, build_turns
from ..audio.subtitles import Cue, clock, to_srt
from ..core.tasks import TaskContext
from ..orchestration.llm import chat_json
from ..orchestration.prompts import MeetingSummary, meeting_messages
from .common import asr_language, llm_session, require_file, transcribe_spans


async def _diarize(ctx: TaskContext, wav16k, params: dict[str, Any]) -> list[Span] | None:
    services = ctx.services
    if params.get("diarize") is False or not services.workers.available("analysis"):
        return None
    spec = services.model_for("diarization", params.get("diarization_model"))
    ctx.progress(0.05, "区分说话人")
    async with services.scheduler.use(spec.id) as handle:
        result = await handle.run(
            "diarize",
            {
                "audio_path": str(wav16k),
                "num_speakers": params.get("num_speakers"),
                "min_speakers": params.get("min_speakers"),
                "max_speakers": params.get("max_speakers"),
                "hf_token": services.secrets.get("hf_token"),
            },
        )
    segments = [
        Span(float(s["start"]), float(s["end"]), s.get("speaker"))
        for s in result.get("segments", [])
    ]
    return build_turns(segments)


async def _transcribe(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    services = ctx.services
    src = require_file(params.get("audio_path"), "音频文件")
    ctx.progress(0.01, "转换音频格式")
    wav16k = services.ffmpeg.to_wav(src, ctx.out_dir / "input16k.wav")
    asr_spec = services.model_for("asr", params.get("model"))
    if (
        asr_spec.worker == "cloud"
        and "diarization" in asr_spec.capabilities
        and params.get("diarize") is not False
    ):
        segments = await _cloud_transcribe_with_speakers(ctx, asr_spec, src, params)
        return _write_transcript(ctx, segments, diarized=True)
    turns = await _diarize(ctx, wav16k, params)
    segments = await transcribe_spans(
        ctx,
        wav16k,
        turns or [],
        model_id=params.get("model"),
        language=params.get("language"),
        progress_range=(0.15, 0.75),
    )
    return _write_transcript(ctx, segments, diarized=turns is not None)


async def _cloud_transcribe_with_speakers(ctx: TaskContext, spec, src, params) -> list[dict]:
    """在线服务一次完成转写和说话人区分（SenseAudio、ElevenLabs）。"""
    services = ctx.services
    services.require_worker(spec)
    ctx.progress(0.1, f"上传到 {spec.name} 转写")
    # 转成 16 kHz 单声道 mp3，体积小，上传快
    upload = services.ffmpeg.to_mp3(src, ctx.out_dir / "upload.mp3")
    async with services.scheduler.use(spec.id) as handle:
        result = await handle.run(
            "asr.transcribe",
            {
                "audio_path": str(upload),
                "language": asr_language(spec, params.get("language")),
                "diarize": True,
                "num_speakers": params.get("num_speakers"),
            },
        )
    segments = result.get("segments") or []
    if not segments and result.get("text"):
        segments = [{"start": 0.0, "end": 0.0, "text": result["text"], "speaker": None}]
    return segments


def _write_transcript(ctx: TaskContext, segments: list[dict], *, diarized: bool) -> dict:
    lines = []
    for seg in segments:
        who = f"{seg['speaker']}：" if seg.get("speaker") else ""
        lines.append(f"[{clock(seg['start'])}] {who}{seg['text']}")
    transcript = "\n".join(lines)
    (ctx.out_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    cues = [Cue(s["start"], s["end"], s["text"], s.get("speaker")) for s in segments]
    (ctx.out_dir / "transcript.srt").write_text(
        to_srt(cues, with_speaker=diarized), encoding="utf-8"
    )
    return {
        "segments": segments,
        "transcript": transcript,
        "diarized": diarized,
        "files": {
            "txt": str(ctx.out_dir / "transcript.txt"),
            "srt": str(ctx.out_dir / "transcript.srt"),
        },
    }


async def transcribe_task(ctx: TaskContext) -> dict[str, Any]:
    return await _transcribe(ctx, ctx.params)


async def meeting_summarize(ctx: TaskContext) -> dict[str, Any]:
    result = await _transcribe(ctx, ctx.params)
    if not result["segments"]:
        return {**result, "summary": None, "warnings": ["没有识别出任何文字"]}
    ctx.progress(0.8, "LLM 生成纪要")
    async with llm_session(ctx.services) as llm:
        summary = await chat_json(llm, meeting_messages(result["transcript"]), MeetingSummary)
    data = summary.model_dump()
    path = ctx.out_dir / "summary.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    result["files"]["summary"] = str(path)
    return {**result, "summary": data}
