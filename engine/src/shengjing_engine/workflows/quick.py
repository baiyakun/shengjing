"""单步任务：快速配音、音效、录音后期、音色文字稿转写。"""

from __future__ import annotations

from typing import Any

from ..core.tasks import TaskContext, UserFacingError
from ..orchestration import compiler
from ..orchestration.llm import chat_json
from ..orchestration.prompts import SfxIdeas, sfx_messages
from ..orchestration.script import CastMember, Line, Script
from .common import job_params, llm_session, require_file, transcribe_spans, voice_ref

EDIT_TYPES = {"emotion", "style", "speed", "denoise", "vad", "paralinguistic"}


async def tts_quick(ctx: TaskContext) -> dict[str, Any]:
    """一句话配音：文字 + 可选音色 + 可选语气 → 一个音频文件。"""
    p = ctx.params
    text = (p.get("text") or "").strip()
    if not text:
        raise UserFacingError("请输入要配音的文字")
    services = ctx.services
    spec = services.model_for("tts", p.get("model"))
    script = Script(
        title="quick",
        language=p.get("language") or "zh",
        cast=[CastMember(id="s", name="说话人", description=p.get("voice_description") or "")],
        lines=[
            Line(
                id="l1",
                speaker="s",
                text=text,
                delivery=p.get("delivery") or "",
                tags=p.get("tags") or [],
                duration=p.get("duration"),
            )
        ],
    )
    voices = {"s": voice_ref(services, p.get("voice_id"))}
    try:
        (job,) = compiler.compile_script(script, spec.id, voices)
    except compiler.CompileError as exc:
        raise UserFacingError(str(exc)) from exc
    out = ctx.out_dir / "speech.wav"
    ctx.progress(0.05, f"加载 {spec.name}")
    async with services.scheduler.use(spec.id) as handle:
        ctx.progress(0.3, "生成中")
        result = await handle.run("tts.generate", job_params(job, out, p.get("options")))
    return {
        "path": result.get("path", str(out)),
        "duration": result.get("duration"),
        "sample_rate": result.get("sample_rate"),
        "model": spec.id,
        "warnings": job.warnings,
    }


async def sfx_generate(ctx: TaskContext) -> dict[str, Any]:
    """生成音效。expand=True 时先让 LLM 把一句需求扩写成多条具体描述。"""
    p = ctx.params
    request = (p.get("description") or "").strip()
    if not request:
        raise UserFacingError("请描述想要的音效")
    variants = max(1, min(int(p.get("variants") or 2), 8))
    duration = float(p.get("duration") or 5.0)
    services = ctx.services
    if p.get("expand"):
        ctx.progress(0.02, "LLM 扩写音效描述")
        async with llm_session(services) as llm:
            ideas = await chat_json(llm, sfx_messages(request, variants), SfxIdeas)
        descriptions = [d for d in ideas.items if d.strip()][:variants] or [request]
    else:
        descriptions = [request] * variants
    spec = services.model_for("sfx", p.get("model"))
    items = []
    async with services.scheduler.use(spec.id) as handle:
        for i, desc in enumerate(descriptions):
            ctx.check_cancelled()
            ctx.progress(0.1 + 0.85 * i / len(descriptions), f"生成第 {i + 1} 条")
            out = ctx.out_dir / f"sfx_{i + 1:02d}.wav"
            result = await handle.run(
                "tts.generate",
                {"text": desc, "output_path": str(out), "duration_seconds": duration},
            )
            items.append({"description": desc, "path": result.get("path", str(out)),
                          "duration": result.get("duration")})
    return {"items": items, "model": spec.id}


async def audio_edit(ctx: TaskContext) -> dict[str, Any]:
    """录音后期：用 Step-Audio-EditX 改情绪、风格、语速，或降噪、去静音、加副语言。"""
    p = ctx.params
    src = require_file(p.get("audio_path"), "录音文件")
    edit_type = p.get("edit_type")
    if edit_type not in EDIT_TYPES:
        raise UserFacingError(f"编辑类型必须是：{'、'.join(sorted(EDIT_TYPES))}")
    if edit_type in {"emotion", "style", "speed"} and not p.get("edit_info"):
        raise UserFacingError("这种编辑需要填写目标，例如 happy、whisper、faster")
    if edit_type == "paralinguistic" and not p.get("target_text"):
        raise UserFacingError("副语言编辑需要填写带标记的目标文字")
    services = ctx.services
    wav = services.ffmpeg.to_wav(src, ctx.out_dir / "input.wav", sample_rate=24000)
    transcript = (p.get("transcript") or "").strip()
    if not transcript:
        ctx.progress(0.05, "先转写原录音")
        wav16 = services.ffmpeg.to_wav(src, ctx.out_dir / "input16k.wav")
        pieces = await transcribe_spans(
            ctx, wav16, [], language=p.get("language"), progress_range=(0.05, 0.3)
        )
        transcript = " ".join(x["text"] for x in pieces)
    spec = services.model_for("edit", p.get("model"))
    out = ctx.out_dir / f"edited_{edit_type}.wav"
    async with services.scheduler.use(spec.id) as handle:
        ctx.progress(0.4, "编辑中")
        result = await handle.run(
            "tts.generate",
            {
                "text": p.get("target_text"),
                "output_path": str(out),
                "reference_audio": str(wav),
                "reference_text": transcript,
                "edit_type": edit_type,
                "edit_info": p.get("edit_info"),
            },
        )
    # input 是转换后放在数据目录里的原录音，界面用它做前后对比播放
    return {"path": result.get("path", str(out)), "input": str(wav),
            "duration": result.get("duration"), "transcript": transcript, "model": spec.id}


async def voice_transcribe(ctx: TaskContext) -> dict[str, Any]:
    """给音色库里的参考录音自动生成文字稿（大部分克隆模型需要它）。"""
    services = ctx.services
    voice = services.voices.get(ctx.params.get("voice_id") or "")
    if voice is None:
        raise UserFacingError("音色不存在")
    wav16 = services.ffmpeg.to_wav(voice["audio_path"], ctx.out_dir / "ref16k.wav")
    pieces = await transcribe_spans(ctx, wav16, [], language=voice.get("language"))
    transcript = "".join(x["text"] for x in pieces).strip()
    if not transcript:
        raise UserFacingError("没有识别出文字，请检查参考录音")
    services.voices.update_transcript(voice["id"], transcript)
    return {"voice_id": voice["id"], "transcript": transcript}
