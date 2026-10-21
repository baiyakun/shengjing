"""各应用的处理流程。每个流程是一个 async 函数，接收 TaskContext，返回结果 dict。"""

from __future__ import annotations

from ..core.tasks import TaskQueue
from . import audiobook, extensions, meeting, music, quick


def register_all(queue: TaskQueue) -> None:
    queue.register("tts.quick", "快速配音", quick.tts_quick)
    queue.register("sfx.generate", "生成音效", quick.sfx_generate)
    queue.register("audio.edit", "录音后期", quick.audio_edit)
    queue.register("voice.transcribe", "音色文字稿转写", quick.voice_transcribe)
    queue.register("asr.transcribe", "语音转文字", meeting.transcribe_task)
    queue.register("meeting.summarize", "会议纪要", meeting.meeting_summarize)
    queue.register("script.generate", "生成剧本", audiobook.script_generate)
    queue.register("audiobook.render", "有声书 / 播客配音", audiobook.audiobook_render)
    queue.register("music.generate", "生成音乐", music.music_generate)
    queue.register("extension.install", "安装扩展包", extensions.install_extension)
