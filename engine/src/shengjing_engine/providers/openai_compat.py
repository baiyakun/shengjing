"""OpenAI 兼容接口：语音合成 /audio/speech、转写 /audio/transcriptions。

OpenAI 官方以及很多国内外服务都兼容这个格式，改接口地址即可使用。
不支持声音克隆；gpt-4o-mini-tts 支持用 instructions 描述语气。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import CloudProvider, OptionInfo, ProviderError


class OpenAICompatProvider(CloudProvider):
    id = "openai"
    name = "OpenAI 兼容接口"
    docs_url = "https://developers.openai.com/api/reference/resources/audio"
    key_url = "https://platform.openai.com/api-keys"
    default_base_url = "https://api.openai.com/v1"
    base_url_editable = True
    options = (
        OptionInfo("tts_model", "语音合成模型", "gpt-4o-mini-tts",
                   "tts-1 / tts-1-hd 不支持语气描述"),
        OptionInfo("tts_voice", "声音", "alloy", "例如 alloy、coral、nova、marin、cedar"),
        OptionInfo("asr_model", "转写模型", "gpt-4o-transcribe", "也可以用 whisper-1"),
    )

    async def tts(self, params: dict[str, Any]) -> dict[str, Any]:
        text = (params.get("text") or "").strip()
        if not text:
            raise ProviderError("没有要合成的文字")
        if len(text) > 4096:
            raise ProviderError("OpenAI 语音合成单次最多 4096 个字符，请拆分")
        body: dict[str, Any] = {
            "model": self.option("tts_model"),
            "input": text,
            "voice": params.get("voice") or self.option("tts_voice"),
            "response_format": "wav",
        }
        if params.get("instructions"):
            body["instructions"] = params["instructions"]
        if params.get("speed"):
            body["speed"] = float(params["speed"])
        async with self.client() as c:
            resp = await c.post("/audio/speech", json=body)
        self.check(resp)
        return self.save_audio(resp.content, "wav", params["output_path"]).to_dict()

    async def asr(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["audio_path"])
        if path.stat().st_size > 24 * 1024 * 1024:
            raise ProviderError("OpenAI 转写单个文件不能超过 25MB")
        data = {"model": self.option("asr_model"), "response_format": "json"}
        if params.get("language"):
            data["language"] = params["language"]
        async with self.client() as c:
            resp = await c.post(
                "/audio/transcriptions",
                data=data,
                files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
            )
        self.check(resp)
        body = resp.json()
        return {"text": body.get("text", ""), "language": params.get("language")}
