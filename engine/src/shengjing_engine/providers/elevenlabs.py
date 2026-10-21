"""ElevenLabs：语音合成（含即时克隆）、转写（可区分说话人）、音效、音乐。

接口文档：https://elevenlabs.io/docs/api-reference
- 语音合成 POST /v1/text-to-speech/{voice_id}
- 即时克隆 POST /v1/voices/add（multipart），返回 voice_id
- 转写 POST /v1/speech-to-text（multipart，diarize 区分说话人，逐词返回 speaker_id）
- 音效 POST /v1/sound-generation（0.5–30 秒）
- 音乐 POST /v1/music（3–600 秒）
请求头为 xi-api-key。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import CloudProvider, OptionInfo, ProviderError, words_to_segments

OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsProvider(CloudProvider):
    id = "elevenlabs"
    name = "ElevenLabs"
    docs_url = "https://elevenlabs.io/docs/api-reference"
    key_url = "https://elevenlabs.io/app/settings/api-keys"
    default_base_url = "https://api.elevenlabs.io"
    options = (
        OptionInfo(
            "tts_model", "语音合成模型", "eleven_v3",
            "eleven_v3 支持 [whispers] [laughs] 等音频标签；也可用 eleven_multilingual_v2",
        ),
        OptionInfo("tts_voice", "默认声音 ID", "JBFqnCBsd6RMkjVDRZzb",
                   "在 ElevenLabs 声音库里复制"),
        OptionInfo("asr_model", "转写模型", "scribe_v2"),
        OptionInfo("sfx_model", "音效模型", "eleven_text_to_sound_v2"),
        OptionInfo("music_model", "音乐模型", "music_v1", "可选 music_v1、music_v2、music_v2_5"),
    )

    def auth_headers(self) -> dict[str, str]:
        return {"xi-api-key": self.ctx.api_key or ""}

    async def _voice_id(self, c, reference: str) -> str:
        fp = self.reference_fingerprint(reference)
        cached = self.ctx.voices.get(self.id, fp)
        if cached:
            return cached
        clip = self.reference_clip(reference, 60.0)
        resp = await c.post(
            "/v1/voices/add",
            data={"name": f"shengjing-{fp[:10]}", "description": "声境音色库导入"},
            files={"files": (clip.name, clip.read_bytes(), "audio/mpeg")},
        )
        self.check(resp)
        body = resp.json()
        voice_id = body.get("voice_id")
        if not voice_id:
            raise ProviderError("ElevenLabs 克隆后没有返回 voice_id")
        if body.get("requires_verification"):
            raise ProviderError("ElevenLabs 要求先在网页上验证这个克隆声音")
        self.ctx.voices.put(self.id, fp, voice_id)
        return voice_id

    async def tts(self, params: dict[str, Any]) -> dict[str, Any]:
        text = (params.get("text") or "").strip()
        if not text:
            raise ProviderError("没有要合成的文字")
        body: dict[str, Any] = {"text": text, "model_id": self.option("tts_model")}
        if params.get("seed") is not None:
            body["seed"] = int(params["seed"]) % 4294967295
        async with self.client() as c:
            voice = params.get("voice") or self.option("tts_voice")
            if params.get("reference_audio"):
                voice = await self._voice_id(c, params["reference_audio"])
            resp = await c.post(
                f"/v1/text-to-speech/{voice}",
                params={"output_format": OUTPUT_FORMAT},
                json=body,
            )
        self.check(resp)
        return self.save_audio(resp.content, "mp3", params["output_path"]).to_dict()

    async def asr(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["audio_path"])
        data: dict[str, Any] = {
            "model_id": self.option("asr_model"),
            "timestamps_granularity": "word",
            "tag_audio_events": "false",
        }
        if params.get("language"):
            data["language_code"] = params["language"]
        if params.get("diarize"):
            data["diarize"] = "true"
            if params.get("num_speakers"):
                data["num_speakers"] = str(params["num_speakers"])
        async with self.client(timeout=1800.0) as c:
            resp = await c.post(
                "/v1/speech-to-text",
                data=data,
                files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
            )
        self.check(resp)
        body = resp.json()
        words = body.get("words") or []
        return {
            "text": body.get("text", ""),
            "language": body.get("language_code"),
            "segments": words_to_segments(words) if params.get("diarize") else [],
        }

    async def sfx(self, params: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"text": params.get("text") or "",
                                "model_id": self.option("sfx_model")}
        if params.get("duration_seconds"):
            body["duration_seconds"] = min(30.0, max(0.5, float(params["duration_seconds"])))
        async with self.client() as c:
            resp = await c.post("/v1/sound-generation", params={"output_format": OUTPUT_FORMAT},
                                json=body)
        self.check(resp)
        return self.save_audio(resp.content, "mp3", params["output_path"]).to_dict()

    async def music(self, params: dict[str, Any]) -> dict[str, Any]:
        lyrics = (params.get("lyrics") or "").strip()
        instrumental = bool(params.get("instrumental")) or not lyrics
        prompt = params.get("style") or params.get("prompt") or ""
        if not instrumental:
            prompt = f"{prompt}\n\nLyrics:\n{lyrics}"
        body: dict[str, Any] = {
            "prompt": prompt,
            "model_id": self.option("music_model"),
            "force_instrumental": instrumental,
        }
        if params.get("duration"):
            body["music_length_ms"] = int(min(600, max(3, float(params["duration"]))) * 1000)
        out_dir = Path(params["output_dir"])
        async with self.client(timeout=900.0) as c:
            resp = await c.post("/v1/music", params={"output_format": OUTPUT_FORMAT}, json=body)
        self.check(resp)
        saved = self.save_audio(resp.content, "mp3", out_dir / "music_01.wav")
        return {"files": [saved.path], "lyrics": lyrics or None, "title": None}
