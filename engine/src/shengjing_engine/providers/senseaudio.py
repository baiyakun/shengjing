"""SenseAudio（商汤）：语音合成、克隆、转写（可区分说话人）、音效、音乐、多角色音频生成。

接口文档：https://docs.senseaudio.cn
- 语音合成 POST /v1/t2a_v2，音频以十六进制字符串放在 data.audio
- 克隆：POST /v1/files/upload（purpose=voice_clone）→ POST /v1/voice/clone，label 即音色 ID
- 转写 POST /v1/audio/transcriptions（文件超过 10MB 先上传，purpose=speech_to_text）
- 音效 POST /v1/sound-effects/generations
- 音乐 POST /v2/music/song/create → GET /v1/music/song/pending/{task_id}
- 音频生成 POST /v1/audio/generate（一次生成多角色对白、配乐和音效，单次最长 120 秒）
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from .base import CloudProvider, OptionInfo, ProviderError

DIRECT_UPLOAD_LIMIT = 10 * 1024 * 1024


class SenseAudioProvider(CloudProvider):
    id = "senseaudio"
    name = "SenseAudio"
    docs_url = "https://docs.senseaudio.cn"
    key_url = "https://senseaudio.cn/api-platform/home"
    default_base_url = "https://api.senseaudio.cn"
    options = (
        OptionInfo("tts_model", "语音合成模型", "sensenova-tts-2.0"),
        OptionInfo("tts_voice", "默认声音", "female_0033_b", "系统音色 ID，见音色广场"),
        OptionInfo("clone_model", "克隆模型", "sensenova-tts-multilingual-2.0"),
        OptionInfo("asr_model", "转写模型", "senseaudio-asr-1.5-260319",
                   "lite 版不支持区分说话人"),
        OptionInfo("sfx_model", "音效模型", "senseaudio-sfx-1.0-260626"),
        OptionInfo("music_model", "音乐模型", "senseaudio-music-2.0-260626"),
        OptionInfo("audiogen_model", "音频生成模型", "senseaudio-a1"),
    )

    @staticmethod
    def _base_ok(body: dict[str, Any]) -> None:
        base = body.get("base_resp") or {}
        if base and int(base.get("status_code", 0)) != 0:
            raise ProviderError(f"SenseAudio 错误：{base.get('status_msg') or base}")

    # ---------- 语音合成和克隆 ----------

    async def _upload(self, c: httpx.AsyncClient, path: Path, purpose: str) -> str:
        resp = await c.post(
            "/v1/files/upload",
            data={"purpose": purpose},
            files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
        )
        self.check(resp)
        body = resp.json()
        self._base_ok(body)
        try:
            return str(body["file"]["file_id"])
        except (KeyError, TypeError) as exc:
            raise ProviderError("SenseAudio 上传文件后没有返回 file_id") from exc

    async def _voice_id(self, c: httpx.AsyncClient, reference: str) -> str:
        fp = self.reference_fingerprint(reference, self.option("clone_model"))
        cached = self.ctx.voices.get(self.id, fp)
        if cached:
            return cached
        clip = self.reference_clip(reference, 30.0)
        file_id = await self._upload(c, clip, "voice_clone")
        label = f"sj_{fp[:16]}"
        resp = await c.post(
            "/v1/voice/clone",
            json={
                "file_id": file_id,
                "model": self.option("clone_model"),
                "text": "你好，这是克隆音色的试听。",
                "label": label,
                "description": "声境音色库导入",
            },
        )
        self.check(resp)
        body = resp.json()
        self._base_ok(body)
        voice_id = str(body.get("label") or label)
        self.ctx.voices.put(self.id, fp, voice_id)
        return voice_id

    async def tts(self, params: dict[str, Any]) -> dict[str, Any]:
        text = (params.get("text") or "").strip()
        if not text:
            raise ProviderError("没有要合成的文字")
        async with self.client() as c:
            voice = params.get("voice") or self.option("tts_voice")
            if params.get("reference_audio"):
                voice = await self._voice_id(c, params["reference_audio"])
            voice_setting: dict[str, Any] = {"voice_id": voice}
            if params.get("speed"):
                voice_setting["speed"] = float(params["speed"])
            resp = await c.post(
                "/v1/t2a_v2",
                json={
                    "model": self.option("tts_model"),
                    "text": text,
                    "stream": False,
                    "voice_setting": voice_setting,
                    "audio_setting": {"format": "wav", "sample_rate": 32000, "channel": 1},
                },
            )
        self.check(resp)
        body = resp.json()
        self._base_ok(body)
        hex_audio = (body.get("data") or {}).get("audio")
        if not hex_audio:
            raise ProviderError("SenseAudio 没有返回音频")
        try:
            audio = bytes.fromhex(hex_audio)
        except ValueError as exc:
            raise ProviderError("SenseAudio 返回的音频无法解码") from exc
        return self.save_audio(audio, "wav", params["output_path"]).to_dict()

    # ---------- 转写 ----------

    async def asr(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["audio_path"])
        data: dict[str, Any] = {
            "model": self.option("asr_model"),
            "response_format": "verbose_json",
        }
        if params.get("language"):
            data["language"] = params["language"]
        if params.get("diarize"):
            data["enable_speaker_diarization"] = "true"
            if params.get("num_speakers"):
                data["max_speakers"] = str(params["num_speakers"])
        async with self.client(timeout=1800.0) as c:
            if path.stat().st_size > DIRECT_UPLOAD_LIMIT:
                data["file_id"] = await self._upload(c, path, "speech_to_text")
                # 接口要求 multipart 格式；没有文件时用 (None, 值) 生成普通表单字段
                resp = await c.post(
                    "/v1/audio/transcriptions",
                    files={k: (None, str(v)) for k, v in data.items()},
                )
            else:
                resp = await c.post(
                    "/v1/audio/transcriptions",
                    data=data,
                    files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
                )
        self.check(resp)
        body = resp.json()
        segments = [
            {
                "start": float(s.get("start", 0)),
                "end": float(s.get("end", 0)),
                "text": str(s.get("text", "")).strip(),
                "speaker": s.get("speaker"),
            }
            for s in body.get("segments") or []
            if str(s.get("text", "")).strip()
        ]
        return {"text": body.get("text", ""), "language": params.get("language"),
                "segments": segments}

    # ---------- 音效 ----------

    async def sfx(self, params: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": params.get("text") or "",
            "model": self.option("sfx_model"),
            "variants_count": 1,
            "output_format": "wav",
        }
        if params.get("duration_seconds"):
            # SenseAudio 音效时长为 1–10 秒的整数
            seconds = round(float(params["duration_seconds"]))
            body["duration_seconds"] = int(min(10, max(1, seconds)))
        async with self.client() as c:
            resp = await c.post("/v1/sound-effects/generations", json=body)
            self.check(resp)
            result = resp.json()
            items = [i for i in result.get("items") or [] if i.get("status") == "completed"]
            if not items or not items[0].get("audio_url"):
                reason = (result.get("items") or [{}])[0].get("fail_reason")
                raise ProviderError(f"SenseAudio 音效生成失败：{reason or result}")
            audio = await self.download(c, items[0]["audio_url"])
        return self.save_audio(audio, items[0].get("output_format") or "wav",
                               params["output_path"]).to_dict()

    # ---------- 音乐 ----------

    async def music(self, params: dict[str, Any]) -> dict[str, Any]:
        instrumental = bool(params.get("instrumental")) or not (params.get("lyrics") or "").strip()
        body: dict[str, Any] = {
            "model": self.option("music_model"),
            "prompt": params.get("prompt") or params.get("style") or "",
            "mode": "instrumental" if instrumental else "sing",
            "style": params.get("style") or "",
            "audio_settings": {"format": "wav"},
        }
        if params.get("duration"):
            body["audio_settings"]["duration"] = int(params["duration"])
        if not instrumental:
            body["lyrics"] = params["lyrics"]
            body["lyrics_settings"] = {"language": params.get("language") or "zh"}
        out_dir = Path(params["output_dir"])
        async with self.client() as c:
            resp = await c.post("/v2/music/song/create", json=body)
            self.check(resp)
            task_id = resp.json().get("task_id")
            if not task_id:
                raise ProviderError("SenseAudio 没有返回音乐任务 ID")
            songs = await self._wait_song(c, str(task_id))
            files = []
            for i, song in enumerate(songs):
                derived = song.get("derived_audio") or {}
                url = derived.get("wav") or song.get("audio_url")
                if not url:
                    continue
                ext = "wav" if derived.get("wav") else Path(url.split("?")[0]).suffix.lstrip(".")
                audio = await self.download(c, url)
                saved = self.save_audio(audio, ext or "mp3", out_dir / f"music_{i + 1:02d}.wav")
                files.append(saved.path)
        if not files:
            raise ProviderError("SenseAudio 音乐任务成功但没有返回音频")
        return {"files": files, "lyrics": songs[0].get("lyrics") if songs else None,
                "title": songs[0].get("title") if songs else None}

    async def _wait_song(self, c: httpx.AsyncClient, task_id: str, timeout: float = 1800.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            resp = await c.get(f"/v1/music/song/pending/{task_id}")
            self.check(resp)
            body = resp.json()
            status = str(body.get("status", "")).upper()
            if status == "SUCCESS":
                return list(((body.get("response") or {}).get("data")) or [])
            if status == "FAILED":
                raise ProviderError(f"SenseAudio 音乐生成失败：{body.get('fail_reason')}")
            await asyncio.sleep(self.ctx.poll_interval)
        raise ProviderError("SenseAudio 音乐生成超时")

    # ---------- 多角色音频生成 ----------

    async def dialogue(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt = params.get("text") or ""
        if len(prompt) > 3000:
            raise ProviderError("SenseAudio 音频生成的描述最多 3000 字，请缩短这一段")
        references = []
        for path in (params.get("references") or [])[:3]:
            clip = self.reference_clip(path, 30.0)
            data = base64.b64encode(clip.read_bytes()).decode()
            references.append({"audio_url": f"data:audio/mpeg;base64,{data}"})
        body = {
            "model": self.option("audiogen_model"),
            "prompt": prompt,
            "references": references,
            "audio_config": {"format": "wav", "sample_rate": 48000, "enable_subtitle": True},
        }
        async with self.client(timeout=600.0) as c:
            resp = await c.post("/v1/audio/generate", json=body)
            self.check(resp)
            result = resp.json()
            url = result.get("audio_url")
            if not url:
                raise ProviderError(f"SenseAudio 音频生成失败：{result}")
            audio = await self.download(c, url)
        saved = self.save_audio(audio, "wav", params["output_path"])
        saved.extra["subtitle"] = result.get("subtitle")
        return saved.to_dict()
