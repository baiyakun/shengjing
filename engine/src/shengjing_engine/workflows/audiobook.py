"""有声书 / 广播剧 / 播客。

script.generate：原文 → LLM 改编成统一剧本 → 自动匹配音色 → 保存为项目
audiobook.render：剧本 → 逐句（或整段对话）配音 → 质检回听，不合格自动重做
                  → 生成音效和配乐 → 混音 → 导出音频和字幕
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..audio import mix
from ..audio.subtitles import Cue as SubCue
from ..audio.subtitles import to_srt
from ..core.tasks import TaskContext, UserFacingError
from ..orchestration import compiler
from ..orchestration.casting import auto_cast
from ..orchestration.llm import chat_json
from ..orchestration.prompts import script_messages
from ..orchestration.qa import check_line
from ..orchestration.script import Script
from .common import asr_language, job_params, llm_session, voice_ref
from .music import AceStepClient, AceStepError

MIX_SR = 48000
MIX_CHANNELS = 2


async def script_generate(ctx: TaskContext) -> dict[str, Any]:
    p = ctx.params
    text = (p.get("text") or "").strip()
    if not text:
        raise UserFacingError("请粘贴要改编的文字")
    style = p.get("style") or "audiobook"
    if style not in ("audiobook", "podcast"):
        raise UserFacingError("style 只能是 audiobook 或 podcast")
    services = ctx.services
    ctx.progress(0.05, "LLM 改编剧本")
    async with llm_session(services) as llm:
        script = await chat_json(
            llm, script_messages(style, text, max_lines=int(p.get("max_lines") or 200)), Script
        )
    if p.get("title"):
        script.title = p["title"]
    ctx.progress(0.85, "自动匹配音色")
    casting = auto_cast(script.cast, services.voices.list())
    for member in script.cast:
        member.voice_id = casting.get(member.id)
    project = services.projects.create(
        style, script.title, {"script": script.model_dump(), "source_text": text}
    )
    return {
        "project_id": project["id"],
        "title": script.title,
        "lines": len(script.lines),
        "cast": [m.model_dump() for m in script.cast],
        "unassigned": [m.name for m in script.cast if not m.voice_id],
    }


@dataclass
class Attempt:
    path: str
    duration: float
    qa: dict[str, Any]


def _expected_text(script: Script, job: compiler.RenderJob) -> str:
    by_id = {line.id: line for line in script.lines}
    return " ".join(by_id[i].text for i in job.line_ids)


def _target_duration(script: Script, job: compiler.RenderJob) -> float | None:
    if len(job.line_ids) != 1 or "duration_seconds" not in job.params:
        return None
    return next(line.duration for line in script.lines if line.id == job.line_ids[0])


def _best(attempts: list[Attempt]) -> int:
    def key(i: int) -> tuple[int, float]:
        qa = attempts[i].qa
        return (0 if qa.get("passed", True) else 1, float(qa.get("error_rate", 0.0)))

    return min(range(len(attempts)), key=key)


async def _render_jobs(
    ctx: TaskContext,
    script: Script,
    jobs: list[compiler.RenderJob],
    tts_id: str,
    lines_dir: Path,
    *,
    qa: bool,
    retries: int,
) -> dict[int, list[Attempt]]:
    services = ctx.services
    attempts: dict[int, list[Attempt]] = {i: [] for i in range(len(jobs))}
    pending = list(range(len(jobs)))
    asr_spec = services.model_for("asr") if qa else None
    rounds = retries + 1 if qa else 1
    total_steps = max(1, len(jobs) * rounds)
    step = 0
    for round_no in range(rounds):
        new: dict[int, Attempt] = {}
        async with services.scheduler.use(tts_id) as handle:
            for i in pending:
                ctx.check_cancelled()
                job = jobs[i]
                out = lines_dir / f"{job.line_ids[0]}_{uuid.uuid4().hex[:6]}.wav"
                extra = {"seed": 1000 + round_no * 17} if round_no else None
                result = await handle.run("tts.generate", job_params(job, out, extra))
                new[i] = Attempt(result.get("path", str(out)), float(result.get("duration", 0)), {})
                step += 1
                label = f"配音 {step}/{len(jobs)}" if round_no == 0 else f"重做第 {round_no} 轮"
                ctx.progress(0.05 + 0.6 * step / total_steps, label)
        if asr_spec is not None:
            lang = asr_language(asr_spec, script.language)
            async with services.scheduler.use(asr_spec.id) as handle:
                for i, attempt in new.items():
                    ctx.check_cancelled()
                    res = await handle.run(
                        "asr.transcribe", {"audio_path": attempt.path, "language": lang}
                    )
                    attempt.qa = check_line(
                        _expected_text(script, jobs[i]),
                        res.get("text") or "",
                        attempt.duration,
                        target_duration=_target_duration(script, jobs[i]),
                    ).to_dict()
        for i, attempt in new.items():
            attempts[i].append(attempt)
        pending = [i for i, a in new.items() if not a.qa.get("passed", True)]
        if not pending:
            break
    return attempts


async def _render_cues(
    ctx: TaskContext, script: Script, spans: dict[str, tuple[float, float]], cue_dir: Path
) -> tuple[dict[str, Path], list[str]]:
    """生成音效和配乐，返回 cue id → 文件。没装音乐扩展包时跳过配乐。"""
    services = ctx.services
    files: dict[str, Path] = {}
    warnings: list[str] = []
    sfx = [c for c in script.cues if c.kind == "sfx"]
    bgm = [c for c in script.cues if c.kind == "bgm"]
    if sfx:
        spec = services.model_for("sfx")
        async with services.scheduler.use(spec.id) as handle:
            for cue in sfx:
                ctx.check_cancelled()
                out = cue_dir / f"{cue.id}.wav"
                await handle.run(
                    "tts.generate",
                    {"text": cue.description, "output_path": str(out),
                     "duration_seconds": cue.duration or 3.0},
                )
                files[cue.id] = out
    if bgm:
        spec = services.model_for("music")
        if not services.workers.available(spec.worker):
            warnings.append("没有安装音乐扩展包，已跳过配乐")
            return files, warnings
        async with services.scheduler.use(spec.id) as handle:
            client = AceStepClient(handle.base_url, api_key=handle.token)
            try:
                for cue in bgm:
                    ctx.check_cancelled()
                    length = cue.duration or _cue_span(cue, script, spans)[1]
                    task_id = await client.release_task(
                        {
                            "prompt": f"{cue.description}, instrumental",
                            "lyrics": "",
                            "audio_duration": float(min(600, max(10, length + 2))),
                            "batch_size": 1,
                            "audio_format": "wav",
                            "task_type": "text2music",
                        }
                    )
                    urls = await client.wait(task_id)
                    if urls:
                        files[cue.id] = await client.download(urls[0], cue_dir / f"{cue.id}.wav")
            except AceStepError as exc:
                warnings.append(f"配乐生成失败，已跳过：{exc}")
            finally:
                await client.aclose()
    return files, warnings


def _cue_span(cue, script: Script, spans: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """返回 (开始时间, 时长)。"""
    first = script.lines[0].id
    last = script.lines[-1].id
    start = spans[cue.at_line or first][0]
    if cue.kind == "bgm":
        end = spans[cue.until_line or last][1]
        return start, max(1.0, end - start)
    return start, cue.duration or 3.0


async def audiobook_render(ctx: TaskContext) -> dict[str, Any]:
    p = ctx.params
    services = ctx.services
    settings = services.settings.get()
    project = services.projects.get(p.get("project_id") or "")
    if project is None:
        raise UserFacingError("项目不存在")
    script = Script.model_validate(project["data"]["script"])
    dialogue = bool(p.get("dialogue", False))
    spec = services.model_for("dialogue" if dialogue else "tts", p.get("model"))
    voices = {m.id: voice_ref(services, m.voice_id) for m in script.cast}
    try:
        all_jobs = compiler.compile_script(script, spec.id, voices, dialogue=dialogue)
    except compiler.CompileError as exc:
        raise UserFacingError(str(exc)) from exc
    warnings = sorted({w for job in all_jobs for w in job.warnings})

    wanted = set(p.get("line_ids") or [])
    selected = services.projects.selected_renders(project["id"])
    to_render = [
        i for i, job in enumerate(all_jobs)
        if (not wanted and (p.get("rerender_all") or job.line_ids[0] not in selected))
        or (wanted and wanted & set(job.line_ids))
    ]
    pdir = services.projects.project_dir(project["id"])
    lines_dir = pdir / "lines"
    lines_dir.mkdir(exist_ok=True)

    qa_on = bool(p.get("qa", settings.qa_enabled))
    sub_jobs = [all_jobs[i] for i in to_render]
    attempts = await _render_jobs(
        ctx, script, sub_jobs, spec.id, lines_dir, qa=qa_on, retries=settings.qa_max_retries
    )
    qa_failed = []
    for local_i, tries in attempts.items():
        job = sub_jobs[local_i]
        best = _best(tries)
        for n, attempt in enumerate(tries):
            services.projects.add_render(
                project["id"], job.line_ids[0], path=attempt.path, model=spec.id,
                duration=attempt.duration, qa={**attempt.qa, "group": job.line_ids},
                select=(n == best),
            )
        if not tries[best].qa.get("passed", True):
            qa_failed.append(job.line_ids[0])

    # ---------- 混音 ----------
    ctx.progress(0.7, "混音")
    selected = services.projects.selected_renders(project["id"])
    missing = [job.line_ids[0] for job in all_jobs if job.line_ids[0] not in selected]
    if missing:
        raise UserFacingError(f"这些台词还没有生成：{', '.join(missing[:10])}")
    ff = services.ffmpeg
    by_line = {line.id: line for line in script.lines}
    units = [ff.decode(selected[j.line_ids[0]]["path"], sample_rate=MIX_SR,
                       channels=MIX_CHANNELS) for j in all_jobs]
    durations = [u.shape[1] / MIX_SR for u in units]
    pauses = [by_line[j.line_ids[-1]].pause_after for j in all_jobs]
    starts = mix.layout_sequential(durations, pauses, start=float(p.get("lead_in", 0.5)))

    spans: dict[str, tuple[float, float]] = {}
    sub_cues: list[SubCue] = []
    cast = script.cast_by_id()
    for job, start, dur in zip(all_jobs, starts, durations, strict=True):
        texts = [by_line[i].text for i in job.line_ids]
        total_chars = sum(len(t) for t in texts) or 1
        t = start
        for line_id, text in zip(job.line_ids, texts, strict=True):
            length = dur * len(text) / total_chars
            spans[line_id] = (t, t + length)
            sub_cues.append(SubCue(t, t + length, text, cast[by_line[line_id].speaker].name))
            t += length

    clips = [mix.Clip(u, s, track="voice") for u, s in zip(units, starts, strict=True)]
    voice_track = mix.render(clips, MIX_SR, MIX_CHANNELS, tail=0.0)
    if p.get("cues", True) and script.cues:
        ctx.progress(0.75, "生成音效和配乐")
        cue_files, cue_warnings = await _render_cues(ctx, script, spans, pdir / "cues")
        warnings += cue_warnings
        for cue in script.cues:
            path = cue_files.get(cue.id)
            if path is None:
                continue
            start, length = _cue_span(cue, script, spans)
            audio = ff.decode(path, sample_rate=MIX_SR, channels=MIX_CHANNELS)
            if cue.kind == "bgm":
                n = int(length * MIX_SR)
                audio = mix.fit_length(audio, n, MIX_SR, loop=True)
                s = int(start * MIX_SR)
                segment = np.zeros_like(audio)
                end = min(voice_track.shape[1], s + n)
                if end > s:
                    segment[:, : end - s] = voice_track[:, s:end]
                audio = mix.duck(audio, segment, MIX_SR)
                clips.append(mix.Clip(audio, start, gain_db=cue.volume_db or -16.0,
                                      fade_in=1.5, fade_out=2.0, track="bgm"))
            else:
                clips.append(mix.Clip(audio, start, gain_db=cue.volume_db or -6.0,
                                      fade_out=0.2, track="sfx"))
    full = mix.peak_limit(mix.render(clips, MIX_SR, MIX_CHANNELS))

    fmt = p.get("format") or "mp3"
    if fmt not in ("mp3", "wav", "m4a", "flac"):
        raise UserFacingError("导出格式只能是 mp3、wav、m4a 或 flac")
    ctx.progress(0.92, "导出")
    out = ctx.out_dir / f"{_safe(script.title)}.{fmt}"
    ff.encode(full, MIX_SR, out, loudness_lufs=settings.export_loudness_lufs)
    srt = out.with_suffix(".srt")
    srt.write_text(to_srt(sub_cues, with_speaker=True), encoding="utf-8")
    return {
        "output": str(out),
        "srt": str(srt),
        "duration": round(full.shape[1] / MIX_SR, 2),
        "rendered": len(sub_jobs),
        "qa_enabled": qa_on,
        "qa_failed": qa_failed,
        "warnings": warnings,
        "model": spec.id,
    }


def _safe(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name).strip()
    return keep[:60] or "output"
