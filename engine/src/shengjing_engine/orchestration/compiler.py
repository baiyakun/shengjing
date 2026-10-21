"""剧本编译器：把统一剧本翻译成各 TTS 模型的写法。

各模型的标签写法（来自 mlx-speech 的模型文档）：

- Fish S2 Pro：句中方括号自由标签，例如 [whisper] [laughing]
- Fish Drama 3：<|speaker:N|> 指定说话人，[语气描述] 放在台词前，支持指定时长
- MOSS-TTSD：[S1] [S2] 说话人标签，统一接口不支持克隆
- VibeVoice：Speaker 1: / Speaker 2: 逐行
- Breeze TTS 2：instruction 参数控制语气，(sigh) (laugh) 两种事件
- DramaBox：一句英文场景描述加引号里的台词，支持指定时长
- 其他模型（dots.tts、FireRedTTS3、MOSS Local、LongCat 等）：纯文本
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .script import CastMember, Line, Script


class CompileError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceRef:
    audio_path: str
    transcript: str = ""


@dataclass
class RenderJob:
    model: str
    line_ids: list[str]
    text: str
    params: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------- 时长估算 ----------------

_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def estimate_seconds(text: str, language: str = "zh") -> float:
    """按自然语速估算朗读时长。

    普通话按每秒约 3.3 个字（不计标点），这是 mlx-speech 文档里 Drama 3
    实测的安全语速；英文按每秒约 2.6 个词。
    """
    cjk = len(_CJK_RE.findall(text))
    words = len(_WORD_RE.findall(_CJK_RE.sub(" ", text)))
    seconds = cjk / 3.3 + words / 2.6
    return round(seconds + 0.3, 2)


# ---------------- 标签翻译 ----------------

_FISH_TAGS = {
    "sigh": "[sigh]",
    "laugh": "[laughing]",
    "pause": "[pause]",
    "whisper": "[whisper]",
    "breath": "[inhale]",
    "cry": "[sobbing]",
}
_DRAMA3_TAGS = {
    "sigh": "[sighs]",
    "laugh": "[laughs]",
    "pause": "[pause]",
    "breath": "[inhales]",
    "cry": "[sobs]",
}
_BREEZE_TAGS = {"sigh": "(sigh)", "laugh": "(laugh)"}
# SenseAudio 音频生成用自然语言描述非语言声音
_ZH_TAGS = {"sigh": "叹气", "laugh": "笑", "pause": "停顿", "whisper": "小声",
            "breath": "吸气", "cry": "哭腔"}
# ElevenLabs v3 的音频标签（英文）
_ELEVEN_TAGS = {
    "sigh": "[sighs]",
    "laugh": "[laughs]",
    "pause": "[pause]",
    "whisper": "[whispers]",
    "breath": "[exhales]",
    "cry": "[crying]",
}


def _fish_text(line: Line) -> str:
    parts = []
    if line.delivery:
        parts.append(f"[{line.delivery}]")
    parts.extend(_FISH_TAGS[t] for t in line.tags)
    parts.append(line.text)
    return " ".join(parts)


def _drama3_text(line: Line) -> str:
    parts = []
    if line.delivery:
        parts.append(f"[{line.delivery}]")
    parts.extend(_DRAMA3_TAGS[t] for t in line.tags if t in _DRAMA3_TAGS)
    body = line.text
    if "whisper" in line.tags:
        body = f"<whisper>{body}</whisper>"
    parts.append(body)
    return " ".join(parts)


# ---------------- 单句编译 ----------------

LineRenderer = Callable[[Line, CastMember, VoiceRef | None, Script], RenderJob]


def _ref_params(voice: VoiceRef | None, *, need_text: bool, model: str) -> dict[str, Any]:
    if voice is None:
        return {}
    if need_text and not voice.transcript.strip():
        raise CompileError(f"{model} 克隆声音需要参考录音的文字稿，请先给这个音色补上文字稿")
    params: dict[str, Any] = {"reference_audio": voice.audio_path}
    if voice.transcript.strip():
        params["reference_text"] = voice.transcript
    return params


def _plain_warnings(line: Line, model: str, *, duration: bool = False) -> list[str]:
    warnings = []
    if line.delivery or line.tags:
        warnings.append(f"{model} 不支持语气描述和标签，已忽略")
    if line.duration is not None and not duration:
        warnings.append(f"{model} 不支持指定时长，已忽略")
    return warnings


def _render_fish_s2(line, cast, voice, script):
    params = _ref_params(voice, need_text=True, model="Fish S2 Pro")
    warnings = []
    if line.duration is not None:
        warnings.append("Fish S2 Pro 不支持指定时长，已忽略")
    return RenderJob("fish-s2-pro", [line.id], _fish_text(line), params, warnings)


def _render_drama3(line, cast, voice, script):
    params = _ref_params(voice, need_text=True, model="Drama 3")
    if line.duration is not None:
        params["duration_seconds"] = line.duration
    return RenderJob("fish-drama-3", [line.id], _drama3_text(line), params, [])


def _render_breeze(line, cast, voice, script):
    params = _ref_params(voice, need_text=True, model="Breeze TTS 2")
    warnings = []
    events = [_BREEZE_TAGS[t] for t in line.tags if t in _BREEZE_TAGS]
    dropped = [t for t in line.tags if t not in _BREEZE_TAGS]
    if dropped:
        warnings.append(f"Breeze TTS 2 只支持叹气和笑声事件，已忽略：{', '.join(dropped)}")
    instruction = line.delivery or (cast.description if voice is None else "")
    if instruction:
        params["instruction"] = instruction
        params["guidance_scale"] = 4.0
    if line.duration is not None:
        warnings.append("Breeze TTS 2 不支持指定时长，已忽略")
    text = " ".join([*events, line.text])
    return RenderJob("breeze-tts-2", [line.id], text, params, warnings)


def _render_dramabox(line, cast, voice, script):
    params = _ref_params(voice, need_text=False, model="DramaBox")
    desc = cast.description.strip() or "A person speaks clearly"
    if line.delivery:
        desc = f"{desc}, {line.delivery}"
    text = line.text.replace('"', "'")
    if line.duration is not None:
        params["duration_seconds"] = line.duration
    warnings = ["DramaBox 的标签已忽略"] if line.tags else []
    return RenderJob("dramabox", [line.id], f'{desc}, "{text}"', params, warnings)


def _plain_renderer(model: str, *, need_text: bool, language_param: str | None = None):
    names = {"zh": "Chinese", "en": "English"}

    def render(line, cast, voice, script):
        params = _ref_params(voice, need_text=need_text, model=model)
        if language_param == "code":
            params["language"] = script.language
        elif language_param == "name":
            params["language"] = names.get(script.language, "Chinese")
        return RenderJob(model, [line.id], line.text, params, _plain_warnings(line, model))

    return render


def _render_openai(line, cast, voice, script):
    params: dict[str, Any] = {}
    warnings = []
    if voice is not None:
        warnings.append("OpenAI 兼容接口不支持克隆，已使用设置里的默认声音")
    instruction = line.delivery or cast.description
    if instruction:
        params["instructions"] = instruction
    if line.tags:
        warnings.append("OpenAI 兼容接口不支持标签，已忽略（语气描述会作为 instructions 传入）")
    if line.duration is not None:
        warnings.append("OpenAI 兼容接口不支持指定时长，已忽略")
    return RenderJob("openai-tts", [line.id], line.text, params, warnings)


def _render_senseaudio(line, cast, voice, script):
    params = _ref_params(voice, need_text=False, model="SenseAudio")
    warnings = []
    text = line.text
    if "pause" in line.tags:
        text = "<break time=600>" + text
    if line.delivery or [t for t in line.tags if t != "pause"]:
        warnings.append("SenseAudio 语音合成不支持语气描述和标签（停顿除外），已忽略；"
                        "需要语气控制可以改用“SenseAudio · 多角色音频生成”")
    if line.duration is not None:
        warnings.append("SenseAudio 语音合成不支持指定时长，已忽略")
    return RenderJob("senseaudio-tts", [line.id], text, params, warnings)


def _render_fish_cloud(line, cast, voice, script):
    params = _ref_params(voice, need_text=True, model="Fish Audio")
    warnings = []
    if line.duration is not None:
        warnings.append("Fish Audio 不支持指定时长，已忽略")
    return RenderJob("fish-audio-tts", [line.id], _fish_text(line), params, warnings)


def _render_elevenlabs(line, cast, voice, script):
    params = _ref_params(voice, need_text=False, model="ElevenLabs")
    parts = []
    if line.delivery:
        parts.append(f"[{line.delivery}]")
    parts.extend(_ELEVEN_TAGS[t] for t in line.tags)
    parts.append(line.text)
    warnings = []
    if line.delivery or line.tags:
        warnings.append("语气描述和标签按 ElevenLabs v3 音频标签发送，旧模型会把它们读出来")
    if line.duration is not None:
        warnings.append("ElevenLabs 不支持指定时长，已忽略")
    return RenderJob("elevenlabs-tts", [line.id], " ".join(parts), params, warnings)


LINE_RENDERERS: dict[str, LineRenderer] = {
    "fish-s2-pro": _render_fish_s2,
    "fish-drama-3": _render_drama3,
    "breeze-tts-2": _render_breeze,
    "dramabox": _render_dramabox,
    "dots-tts-soar": _plain_renderer("dots-tts-soar", need_text=False, language_param="code"),
    "dots-tts-mf": _plain_renderer("dots-tts-mf", need_text=False, language_param="code"),
    "fireredtts3-base": _plain_renderer("fireredtts3-base", need_text=True, language_param="name"),
    "moss-local": _plain_renderer("moss-local", need_text=False),
    "longcat": _plain_renderer("longcat", need_text=False),
    "step-audio": _plain_renderer("step-audio", need_text=True),
    "openai-tts": _render_openai,
    "senseaudio-tts": _render_senseaudio,
    "fish-audio-tts": _render_fish_cloud,
    "elevenlabs-tts": _render_elevenlabs,
}

# ---------------- 多人对话编译 ----------------

DIALOGUE_MODELS = {"fish-drama-3", "moss-ttsd", "vibevoice", "senseaudio-audiogen"}

# SenseAudio 音频生成单次最长 120 秒，按普通话每秒约 3 个字留余量
SENSEAUDIO_CHUNK_CHARS = 330


def _chunks(lines: list[Line], max_chars: int) -> list[list[Line]]:
    groups: list[list[Line]] = []
    current: list[Line] = []
    size = 0
    for line in lines:
        if current and size + len(line.text) > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line.text)
    if current:
        groups.append(current)
    return groups


def _speaker_index(script: Script) -> dict[str, int]:
    order: dict[str, int] = {}
    for line in script.lines:
        if line.speaker not in order:
            order[line.speaker] = len(order)
    return order


def _compile_dialogue(
    script: Script, model: str, voices: dict[str, VoiceRef | None], max_chars: int
) -> list[RenderJob]:
    index = _speaker_index(script)
    cast = script.cast_by_id()
    if model == "senseaudio-audiogen":
        max_chars = min(max_chars, SENSEAUDIO_CHUNK_CHARS)
    jobs: list[RenderJob] = []
    for group in _chunks(script.lines, max_chars):
        warnings: list[str] = []
        ids = [line.id for line in group]
        if model == "fish-drama-3":
            if len(index) > 16:
                raise CompileError("Drama 3 最多支持 16 个角色")
            text = " ".join(f"<|speaker:{index[ln.speaker]}|> {_drama3_text(ln)}" for ln in group)
            speakers = sorted({ln.speaker for ln in group}, key=lambda s: index[s])
            audios, texts, ids_ = [], [], []
            for sp in speakers:
                voice = voices.get(sp)
                if voice is None:
                    continue
                if not voice.transcript.strip():
                    raise CompileError("Drama 3 克隆声音需要参考录音的文字稿")
                audios.append(voice.audio_path)
                texts.append(voice.transcript)
                ids_.append(index[sp])
            params: dict[str, Any] = {}
            if audios:
                params = {
                    "reference_audio": audios,
                    "reference_text": texts,
                    "reference_speaker": ids_,
                }
            if any(ln.duration is not None for ln in group):
                warnings.append("对话模式下无法逐句指定时长，已忽略")
            jobs.append(RenderJob(model, ids, text, params, warnings))
        elif model == "moss-ttsd":
            text = "".join(f"[S{index[ln.speaker] + 1}]{ln.text}" for ln in group)
            if any(voices.get(sp) for sp in index):
                warnings.append("MOSS-TTSD 统一接口暂不支持克隆，已使用模型自带声音")
            if len(index) > 2:
                warnings.append("超过 2 个说话人时 MOSS-TTSD 的效果尚未验证")
            if any(ln.delivery or ln.tags for ln in group):
                warnings.append("MOSS-TTSD 不支持语气描述和标签，已忽略")
            jobs.append(RenderJob(model, ids, text, {}, warnings))
        elif model == "vibevoice":
            if len(index) > 4:
                raise CompileError("VibeVoice 最多支持 4 个说话人")
            text = "\n".join(f"Speaker {index[ln.speaker] + 1}: {ln.text}" for ln in group)
            if any(voices.get(sp) for sp in index):
                warnings.append("统一接口下 VibeVoice 不能分别克隆每个人，已使用模型自带声音")
            jobs.append(RenderJob(model, ids, text, {}, warnings))
        elif model == "senseaudio-audiogen":
            text, params, warnings = _senseaudio_prompt(group, cast, voices)
            jobs.append(RenderJob(model, ids, text, params, warnings))
        else:
            raise CompileError(f"{model} 不是多人对话模型")
    return jobs


def _senseaudio_prompt(
    group: list[Line], cast: dict[str, CastMember], voices: dict[str, VoiceRef | None]
) -> tuple[str, dict[str, Any], list[str]]:
    """SenseAudio 音频生成的写法：角色列表 → 音频内容，{speaker：角色名}（语气）台词。

    参考音色用 @音频N 引用，最多 3 个；文字里的 @ 需要转义。
    """
    speakers: list[str] = []
    for line in group:
        if line.speaker not in speakers:
            speakers.append(line.speaker)
    refs: list[str] = []
    roles: list[str] = []
    warnings: list[str] = []
    for sp in speakers:
        member = cast[sp]
        desc = member.description.strip() or "自然的声音"
        voice = voices.get(sp)
        if voice is not None and len(refs) < 3:
            refs.append(voice.audio_path)
            desc += f"，音色参考 @音频{len(refs)}"
        elif voice is not None:
            warnings.append(f"SenseAudio 一次最多 3 个参考音色，{member.name} 改用文字描述")
        roles.append(f"- {member.name}：{desc}")
    body = []
    for line in group:
        cues = [line.delivery] if line.delivery else []
        cues += [_ZH_TAGS[t] for t in line.tags]
        cue = f"（{'，'.join(cues)}）" if cues else ""
        text = line.text.replace("@", "\\@")
        body.append(f"{{speaker：{cast[line.speaker].name}}}{cue}{text}")
    if any(line.duration is not None for line in group):
        warnings.append("SenseAudio 音频生成不支持逐句指定时长，已忽略")
    prompt = "角色列表：\n" + "\n".join(roles) + "\n\n音频内容：\n" + "\n".join(body)
    return prompt, ({"references": refs} if refs else {}), warnings


# ---------------- 入口 ----------------


def compile_script(
    script: Script,
    model: str,
    voices: dict[str, VoiceRef | None],
    *,
    dialogue: bool = False,
    max_chars: int = 400,
) -> list[RenderJob]:
    """把剧本编译成渲染任务。

    dialogue=False（默认）：逐句生成，每句一个任务，可以单独重做某一句。
    dialogue=True：多人对话模型一次生成一段，轮换更自然，但只能整段重做。
    """
    if dialogue:
        if model not in DIALOGUE_MODELS:
            raise CompileError(f"{model} 不支持多人对话模式")
        return _compile_dialogue(script, model, voices, max_chars)

    renderer = LINE_RENDERERS.get(model)
    if renderer is None:
        raise CompileError(f"{model} 不支持逐句生成")
    cast = script.cast_by_id()
    jobs = []
    for line in script.lines:
        job = renderer(line, cast[line.speaker], voices.get(line.speaker), script)
        if line.duration is not None:
            estimate = estimate_seconds(line.text, script.language)
            if line.duration < estimate * 0.9:
                job.warnings.append(
                    f"目标时长 {line.duration:.1f} 秒可能太短（自然语速约需 {estimate:.1f} 秒），"
                    "句尾可能被吞掉"
                )
        jobs.append(job)
    return jobs


def strip_markup(text: str) -> str:
    """去掉各种标签，得到实际要读的文字（质检对比用）。"""
    text = re.sub(r"<\|speaker:\d+\|>", " ", text)
    text = re.sub(r"</?whisper>", "", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\((sigh|laugh)\)", " ", text)
    return re.sub(r"\s+", " ", text).strip()
