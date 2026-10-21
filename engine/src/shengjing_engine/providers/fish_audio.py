"""Fish Audio 官方 API：语音合成（含零样本克隆）、转写。

接口文档：https://docs.fish.audio
- 语音合成 POST /v1/tts，请求头 model 选择模型（s2.1-pro、drama-3 等）
  直接带参考录音克隆时，请求体必须用 MessagePack（不支持 JSON 里放 base64）
- 转写 POST /v1/asr，multipart 上传
Fish S2 系列支持和本地 Fish S2 Pro 一样的行内标签，例如 [whisper]。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgpack

from .base import CloudProvider, OptionInfo, ProviderError


class FishAudioProvider(CloudProvider):
    id = "fish_audio"
    name = "Fish Audio"
    docs_url = "https://docs.fish.audio"
    key_url = "https://fish.audio/app/api-keys"
    default_base_url = "https://api.fish.audio"
    options = (
        OptionInfo("tts_model", "语音合成模型", "s2.1-pro", "可选 s1、s2-pro、s2.1-pro、drama-3"),
        OptionInfo("tts_voice", "默认声音", "", "Fish Audio 声音库里的模型 ID；留空由模型自选"),
        OptionInfo("asr_model", "转写模型", "transcribe-1-pro"),
    )

    async def tts(self, params: dict[str, Any]) -> dict[str, Any]:
        text = (params.get("text") or "").strip()
        if not text:
            raise ProviderError("没有要合成的文字")
        body: dict[str, Any] = {"text": text, "format": "wav", "normalize": True,
                                "latency": "normal"}
        headers = {"model": self.option("tts_model")}
        ref = params.get("reference_audio")
        if ref:
            if not params.get("reference_text"):
                raise ProviderError("Fish Audio 克隆声音需要参考录音的文字稿")
            clip = self.reference_clip(ref, 30.0)
            body["references"] = [{"audio": clip.read_bytes(), "text": params["reference_text"]}]
            content = msgpack.packb(body, use_bin_type=True)
            headers["Content-Type"] = "application/msgpack"
        else:
            voice = params.get("voice") or self.option("tts_voice")
            if voice:
                body["reference_id"] = voice
            content = None
        async with self.client() as c:
            if content is not None:
                resp = await c.post("/v1/tts", content=content, headers=headers)
            else:
                resp = await c.post("/v1/tts", json=body, headers=headers)
        self.check(resp)
        return self.save_audio(resp.content, "wav", params["output_path"]).to_dict()

    async def asr(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["audio_path"])
        data = {"ignore_timestamps": "true"}
        if params.get("language"):
            data["language"] = params["language"]
        async with self.client(timeout=1800.0) as c:
            resp = await c.post(
                "/v1/asr",
                data=data,
                files={"audio": (path.name, path.read_bytes(), "application/octet-stream")},
                headers={"model": self.option("asr_model")},
            )
        self.check(resp)
        body = resp.json()
        return {"text": body.get("text", ""), "language": body.get("language_code")}
