"""模型目录：每个模型的用途、所在进程、许可证、商用限制和内存估算。

这里是全项目唯一的模型清单。README 里的许可证表、界面里的“模型管理”页、
调度器的内存预算，全部以这里为准。改动许可证信息时请同时核对模型卡原文。

内存说明：
- measured=True 的数值来自 mlx-speech 文档里的实测峰值。
- 其余为估算：权重大小 × 1.3 + 1 GB，权重大小取自 Hugging Face 仓库文件大小。
  第 0 阶段在真实 Mac 上实测后要更新。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Kind = Literal[
    "tts",  # 单人朗读 / 克隆
    "dialogue",  # 多人对话一次生成
    "voice_design",  # 用文字描述造声音
    "edit",  # 修改已有录音
    "sfx",  # 音效
    "asr",  # 离线转写
    "asr_stream",  # 实时转写
    "llm",
    "diarization",
    "diarization_stream",
    "alignment",
    "separation",
    "vad",
    "music",
]

Worker = Literal["speech", "llm", "analysis", "music", "cloud"]
"""cloud 表示在线 API，source 为服务商 ID（见 providers/）。"""

# yes: 许可证允许商用（可能有署名等义务）
# restricted: 有条件商用（营收门槛、规模限制等），商用前必须逐条核对
# no: 非商用，或商用必须另外取得授权
Commercial = Literal["yes", "restricted", "no"]

Edition = Literal["personal", "commercial"]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    kind: Kind
    worker: Worker
    source: str
    """加载用的标识：mlx-speech 别名、Hugging Face 仓库 ID 或本地路径。"""
    url: str
    license: str
    commercial: Commercial
    memory_gb: float
    measured: bool = False
    languages: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    license_note: str = ""
    notes: str = ""
    load_options: dict = field(default_factory=dict)
    needs_conversion: bool = False
    """True 表示没有现成的 MLX 权重，需要先用 mlx-speech 的转换脚本转换。"""

    def allowed_in(self, edition: Edition) -> bool:
        if edition == "personal":
            return True
        return self.commercial != "no"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["languages"] = list(self.languages)
        data["capabilities"] = list(self.capabilities)
        return data


FISH_LICENSE_NOTE = (
    "Fish Audio Research License：研究和非商用免费；任何商用（含在线服务）"
    "都必须另外取得 Fish Audio 的书面授权。分发时需附许可证并标注 "
    "“Built with Fish Audio”。"
)

_MODELS: tuple[ModelSpec, ...] = (
    # ---------------- 语音进程：TTS ----------------
    ModelSpec(
        id="fish-s2-pro",
        name="Fish S2 Pro",
        kind="tts",
        worker="speech",
        source="fish-s2-pro",
        url="https://huggingface.co/appautomaton/fishaudio-s2-pro-8bit-mlx",
        license="Fish Audio Research License",
        commercial="no",
        memory_gb=9.0,
        languages=("zh", "en"),
        capabilities=("clone", "emotion_tags"),
        license_note=FISH_LICENSE_NOTE,
        notes="行内情绪标签，如 [whisper] [laughing]，训练过 1.5 万多种标签。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="fish-drama-3",
        name="Fish Audio Drama 3",
        kind="dialogue",
        worker="speech",
        source="models/fish_drama_3/mlx-int8",
        url="https://huggingface.co/fishaudio/drama-3",
        license="Fish Audio Research License",
        commercial="no",
        memory_gb=46.0,
        measured=True,
        languages=("zh", "en", "ja", "ko"),
        capabilities=("clone", "multi_speaker", "delivery_text", "duration"),
        license_note=FISH_LICENSE_NOTE,
        notes="最多 16 个角色；可指定时长；需要 64GB 以上内存，权重需自行转换。",
        load_options={"kind": "tts"},
        needs_conversion=True,
    ),
    ModelSpec(
        id="dots-tts-soar",
        name="dots.tts SOAR (int8)",
        kind="tts",
        worker="speech",
        source="dots-tts-soar",
        url="https://huggingface.co/appautomaton/dots-tts-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=7.0,
        measured=True,
        languages=("zh", "en"),
        capabilities=("clone", "clone_audio_only", "stream"),
        notes="中英文通过官方验证；可以只给参考录音、不给文字稿。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="dots-tts-mf",
        name="dots.tts MeanFlow (int8)",
        kind="tts",
        worker="speech",
        source="dots-tts-mf",
        url="https://huggingface.co/appautomaton/dots-tts-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=7.2,
        measured=True,
        languages=("zh", "en"),
        capabilities=("clone", "clone_audio_only", "stream"),
        notes="4 步蒸馏版本，速度更快。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="fireredtts3-base",
        name="FireRedTTS3 Base",
        kind="tts",
        worker="speech",
        source="fireredtts3-base",
        url="https://huggingface.co/appautomaton/fireredtts3-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=8.5,
        languages=("zh", "en", "yue", "ja", "ko", "fr", "de", "es"),
        capabilities=("clone",),
        notes="多语种克隆，24 kHz。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="moss-local",
        name="OpenMOSS TTS Local",
        kind="tts",
        worker="speech",
        source="moss-local",
        url="https://huggingface.co/appautomaton/openmoss-tts-local-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=7.5,
        languages=("zh", "en"),
        capabilities=("clone",),
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="longcat",
        name="LongCat AudioDiT 3.5B",
        kind="tts",
        worker="speech",
        source="longcat",
        url="https://huggingface.co/appautomaton/longcat-audiodit-3.5b-8bit-mlx",
        license="MIT",
        commercial="yes",
        memory_gb=7.0,
        languages=("zh", "en"),
        capabilities=("clone",),
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="vibevoice",
        name="VibeVoice Large",
        kind="dialogue",
        worker="speech",
        source="vibevoice",
        url="https://huggingface.co/appautomaton/vibevoice-mlx",
        license="Apache-2.0（MLX 权重卡标注）",
        commercial="yes",
        memory_gb=13.0,
        languages=("zh", "en"),
        capabilities=("clone", "multi_speaker", "long_form"),
        license_note="上游为微软 VibeVoice，商用前请复核上游的使用声明。",
        notes="统一接口下多人对话不能分别克隆每个人的声音。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="moss-ttsd",
        name="MOSS-TTSD v1.0",
        kind="dialogue",
        worker="speech",
        source="moss-ttsd",
        url="https://huggingface.co/appautomaton/openmoss-ttsd-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=14.0,
        languages=("zh", "en"),
        capabilities=("multi_speaker",),
        notes="文本必须带 [S1]/[S2]；统一接口暂不支持克隆；长对话会跑偏，需分段。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="dramabox",
        name="DramaBox (48 kHz 立体声)",
        kind="tts",
        worker="speech",
        source="dramabox",
        url="https://huggingface.co/appautomaton/dramabox-tts-3.3b-bf16-mlx",
        license="LTX-2 Community License + Gemma Terms of Use",
        commercial="restricted",
        memory_gb=19.0,
        languages=("en",),
        capabilities=("clone", "duration", "stereo"),
        license_note=(
            "LTX-2 Community License：年收入 1000 万美元以上的实体需付费授权；"
            "禁止未经同意模仿真人，发布机器生成内容需明确标注。"
            "文本编码器为 Gemma 3 12B，另受 Gemma 使用条款约束。"
        ),
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="breeze-tts-2",
        name="Breeze TTS 2",
        kind="voice_design",
        worker="speech",
        source="models/breezeblue/breeze_tts_2/mlx-bf16",
        url="https://huggingface.co/BreezeBlue/Breeze-TTS-2",
        license="BreezeBlue Research and Non-Commercial License",
        commercial="no",
        memory_gb=9.2,
        measured=True,
        languages=("zh", "en"),
        capabilities=("voice_design", "instruction", "clone", "stream"),
        license_note="仅限研究和非商用，生成的音频同样受此限制。",
        notes="用文字描述造声音、用指令控制语气；权重需自行转换。",
        load_options={"kind": "tts"},
        needs_conversion=True,
    ),
    ModelSpec(
        id="step-audio",
        name="Step-Audio-EditX",
        kind="edit",
        worker="speech",
        source="step-audio",
        url="https://huggingface.co/appautomaton/step-audio-editx-8bit-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=6.5,
        languages=("zh", "en", "ja", "ko"),
        capabilities=("edit", "clone"),
        notes="编辑类型：emotion / style / speed / denoise / vad / paralinguistic。",
        load_options={"kind": "tts"},
    ),
    ModelSpec(
        id="moss-sound-effect",
        name="MOSS-SoundEffect",
        kind="sfx",
        worker="speech",
        source="moss-sound-effect",
        url="https://huggingface.co/appautomaton/openmoss-sound-effect-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=9.0,
        capabilities=("duration",),
        notes="文字描述生成音效和环境声；官方说明不适合做音乐。",
        load_options={"kind": "tts"},
    ),
    # ---------------- 语音进程：ASR ----------------
    ModelSpec(
        id="qwen3-asr-1.7b",
        name="Qwen3-ASR 1.7B (int8)",
        kind="asr",
        worker="speech",
        source="qwen3-asr-1.7b",
        url="https://huggingface.co/appautomaton/qwen3-asr-1.7b-int8-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=4.0,
        languages=("zh", "en"),
        notes="中文、英文和中英混合。",
        load_options={"kind": "asr"},
    ),
    ModelSpec(
        id="cohere-asr",
        name="Cohere Transcribe",
        kind="asr",
        worker="speech",
        source="cohere-asr",
        url="https://huggingface.co/appautomaton/cohere-asr-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=4.0,
        notes="长音频快（实测约 61 倍实时）。",
        load_options={"kind": "asr"},
    ),
    ModelSpec(
        id="granite-speech-4.0-1b",
        name="IBM Granite Speech 4.0 1B",
        kind="asr",
        worker="speech",
        source="granite-speech-4.0-1b",
        url="https://huggingface.co/appautomaton/granite-4.0-1b-speech-int8-mlx",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=4.5,
        languages=("en", "fr", "de", "es", "pt", "ja"),
        notes="不支持中文。",
        load_options={"kind": "asr"},
    ),
    ModelSpec(
        id="confucius4-r2t2",
        name="Confucius4-R2T2",
        kind="asr_stream",
        worker="speech",
        source="confucius4-r2t2",
        url="https://huggingface.co/appautomaton/confucius4-r2t2-bf16-mlx",
        license="NetEase Youdao Model Use License Agreement",
        commercial="restricted",
        memory_gb=6.0,
        languages=("zh", "en", "yue", "ja", "ko"),
        capabilities=("stream",),
        license_note="大规模商用需要另外取得网易有道授权；禁止高风险用途。",
        notes="实时流式转写，30 种语言。",
        load_options={"kind": "asr"},
    ),
    ModelSpec(
        id="nemotron-asr-streaming",
        name="NVIDIA Nemotron 3.5 ASR Streaming",
        kind="asr_stream",
        worker="speech",
        source="nemotron-asr-streaming",
        url="https://huggingface.co/appautomaton/nemotron-3.5-asr-streaming-0.6b-int8-mlx",
        license="OpenMDW-1.1",
        commercial="yes",
        memory_gb=2.0,
        languages=("en", "zh", "ja", "ko", "fr", "de", "es"),
        capabilities=("stream",),
        license_note="再分发时需保留许可证副本。",
        load_options={"kind": "asr"},
    ),
    # ---------------- LLM 进程 ----------------
    ModelSpec(
        id="qwen3-4b-4bit",
        name="Qwen3 4B (4bit)",
        kind="llm",
        worker="llm",
        source="mlx-community/Qwen3-4B-4bit",
        url="https://huggingface.co/mlx-community/Qwen3-4B-4bit",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=4.0,
        notes="16–18 GB 机器推荐的本地 LLM。",
    ),
    ModelSpec(
        id="qwen3.5-9b-4bit",
        name="Qwen3.5 9B (4bit)",
        kind="llm",
        worker="llm",
        source="mlx-community/Qwen3.5-9B-4bit",
        url="https://huggingface.co/mlx-community/Qwen3.5-9B-4bit",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=8.0,
        notes="24–48 GB 机器推荐的本地 LLM。",
    ),
    ModelSpec(
        id="qwen3.6-35b-a3b-4bit",
        name="Qwen3.6 35B-A3B (4bit)",
        kind="llm",
        worker="llm",
        source="mlx-community/Qwen3.6-35B-A3B-4bit",
        url="https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-4bit",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=24.0,
        notes="64 GB 以上机器推荐的本地 LLM（MoE，每个 token 只激活约 3B 参数）。",
    ),
    # ---------------- 分析进程（扩展包） ----------------
    ModelSpec(
        id="pyannote-community-1",
        name="pyannote speaker-diarization-community-1",
        kind="diarization",
        worker="analysis",
        source="pyannote/speaker-diarization-community-1",
        url="https://huggingface.co/pyannote/speaker-diarization-community-1",
        license="CC-BY-4.0（代码 pyannote.audio 为 MIT）",
        commercial="yes",
        memory_gb=2.0,
        license_note="需要署名；下载前要在 Hugging Face 页面同意使用条款并提供访问令牌。",
        notes="离线说话人区分。",
    ),
    ModelSpec(
        id="sortformer-streaming-v2",
        name="NVIDIA Streaming Sortformer 4spk v2",
        kind="diarization_stream",
        worker="analysis",
        source="nvidia/diar_streaming_sortformer_4spk-v2",
        url="https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2",
        license="CC-BY-4.0",
        commercial="yes",
        memory_gb=2.0,
        license_note="需要署名。注意 v1 版本是 CC-BY-NC（非商用），不要混用。",
        notes="实时说话人区分，最多 4 人。第 4 阶段接入。",
    ),
    ModelSpec(
        id="qwen3-forced-aligner",
        name="Qwen3-ForcedAligner 0.6B",
        kind="alignment",
        worker="analysis",
        source="Qwen/Qwen3-ForcedAligner-0.6B",
        url="https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B",
        license="Apache-2.0",
        commercial="yes",
        memory_gb=3.5,
        languages=("zh", "en", "yue", "ja", "ko", "fr", "de", "es", "it", "pt", "ru"),
        notes="词级时间戳；单段最长约 5 分钟；官方示例只给了 CUDA 用法，Mac 上需验证。",
    ),
    ModelSpec(
        id="htdemucs",
        name="HT-Demucs",
        kind="separation",
        worker="analysis",
        source="htdemucs",
        url="https://github.com/adefossez/demucs",
        license="MIT",
        commercial="yes",
        memory_gb=3.0,
        notes="人声和伴奏分离，用于翻译配音保留背景音。",
    ),
    ModelSpec(
        id="silero-vad",
        name="Silero VAD",
        kind="vad",
        worker="analysis",
        source="silero-vad",
        url="https://github.com/snakers4/silero-vad",
        license="MIT",
        commercial="yes",
        memory_gb=0.5,
        notes="检测哪里有人声。",
    ),
    # ---------------- 音乐进程（扩展包） ----------------
    ModelSpec(
        id="ace-step-1.5",
        name="ACE-Step 1.5",
        kind="music",
        worker="music",
        source="acestep-v15-turbo",
        url="https://huggingface.co/ACE-Step/Ace-Step1.5",
        license="MIT",
        commercial="yes",
        memory_gb=12.0,
        languages=("zh", "en", "ja"),
        capabilities=("lyrics", "cover", "repaint"),
        license_note="模型卡写明生成的音乐可以商用。",
        notes="带人声的完整歌曲，10 秒到 10 分钟；中文歌词效果需实测。",
    ),
)

CLOUD_NOTE = "在线服务，按服务商的价格计费；音频和文字会上传到服务商。"


def _cloud(
    id: str,
    name: str,
    kind: Kind,
    provider: str,
    url: str,
    *,
    capabilities: tuple[str, ...] = (),
    notes: str = "",
) -> ModelSpec:
    return ModelSpec(
        id=id,
        name=name,
        kind=kind,
        worker="cloud",
        source=provider,
        url=url,
        license="服务商条款",
        commercial="restricted",
        memory_gb=0.0,
        capabilities=capabilities,
        license_note=CLOUD_NOTE,
        notes=notes,
    )


_CLOUD_MODELS: tuple[ModelSpec, ...] = (
    # OpenAI 兼容接口（改接口地址可用于其他兼容服务）
    _cloud("openai-tts", "OpenAI 兼容 · 语音合成", "tts", "openai",
           "https://developers.openai.com/api/reference/resources/audio",
           capabilities=("instruction",), notes="不支持克隆；gpt-4o-mini-tts 可用文字描述语气。"),
    _cloud("openai-asr", "OpenAI 兼容 · 转写", "asr", "openai",
           "https://developers.openai.com/api/reference/resources/audio"),
    # SenseAudio（商汤）
    _cloud("senseaudio-tts", "SenseAudio · 语音合成", "tts", "senseaudio",
           "https://docs.senseaudio.cn/guides/tts/overview", capabilities=("clone",),
           notes="参考录音会自动克隆成 SenseAudio 音色（只克隆一次）。"),
    _cloud("senseaudio-audiogen", "SenseAudio · 多角色音频生成", "dialogue", "senseaudio",
           "https://docs.senseaudio.cn/guides/audio/overview",
           capabilities=("multi_speaker", "clone", "delivery_text"),
           notes="一次生成多角色对白；单次最长 120 秒，最多 3 个参考音色。"),
    _cloud("senseaudio-asr", "SenseAudio · 转写", "asr", "senseaudio",
           "https://docs.senseaudio.cn/guides/asr/overview", capabilities=("diarization",),
           notes="可以同时区分说话人。"),
    _cloud("senseaudio-sfx", "SenseAudio · 音效", "sfx", "senseaudio",
           "https://docs.senseaudio.cn/guides/sfx/overview", notes="1–10 秒。"),
    _cloud("senseaudio-music", "SenseAudio · 音乐", "music", "senseaudio",
           "https://docs.senseaudio.cn/guides/music/overview"),
    # Fish Audio
    _cloud("fish-audio-tts", "Fish Audio · 语音合成", "tts", "fish_audio",
           "https://docs.fish.audio", capabilities=("clone", "emotion_tags"),
           notes="和本地 Fish S2 Pro 一样支持行内情绪标签；也可在设置里选 drama-3 模型。"),
    _cloud("fish-audio-asr", "Fish Audio · 转写", "asr", "fish_audio", "https://docs.fish.audio"),
    # ElevenLabs
    _cloud("elevenlabs-tts", "ElevenLabs · 语音合成", "tts", "elevenlabs",
           "https://elevenlabs.io/docs/api-reference/text-to-speech/convert",
           capabilities=("clone", "audio_tags"),
           notes="参考录音会自动做即时克隆（只克隆一次）。"),
    _cloud("elevenlabs-asr", "ElevenLabs · 转写", "asr", "elevenlabs",
           "https://elevenlabs.io/docs/api-reference/speech-to-text/convert",
           capabilities=("diarization",), notes="可以同时区分说话人。"),
    _cloud("elevenlabs-sfx", "ElevenLabs · 音效", "sfx", "elevenlabs",
           "https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert",
           notes="0.5–30 秒。"),
    _cloud("elevenlabs-music", "ElevenLabs · 音乐", "music", "elevenlabs",
           "https://elevenlabs.io/docs/api-reference/music/compose"),
)

_MODELS = _MODELS + _CLOUD_MODELS

CATALOG: dict[str, ModelSpec] = {spec.id: spec for spec in _MODELS}


def get(model_id: str) -> ModelSpec:
    try:
        return CATALOG[model_id]
    except KeyError as exc:
        raise KeyError(f"未知模型：{model_id}") from exc


def models_for(kind: str, edition: Edition) -> list[ModelSpec]:
    return [m for m in _MODELS if m.kind == kind and m.allowed_in(edition)]


def all_models() -> list[ModelSpec]:
    return list(_MODELS)
