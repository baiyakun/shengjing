"""在线服务商的公共部分。

每家服务商实现 tts / asr / sfx / music / dialogue 中它支持的几项。
工作流不直接调用这些方法，而是像调用本地模型一样调用 handle.run(op, params)，
由 CloudHandle 按模型类型转到对应方法，所以本地模型和在线 API 可以随时互换。
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import httpx

from ..audio.ffmpeg import FFmpeg, wav_duration
from ..core.db import CloudVoiceStore


class ProviderError(RuntimeError):
    """可以直接展示给用户的在线服务错误。"""


@dataclass
class ProviderContext:
    api_key: str | None
    base_url: str
    options: dict[str, str]
    ffmpeg: FFmpeg
    voices: CloudVoiceStore
    tmp_dir: Path
    transport: httpx.AsyncBaseTransport | None = None
    poll_interval: float = 3.0


@dataclass(frozen=True)
class OptionInfo:
    key: str
    label: str
    default: str
    hint: str = ""


@dataclass
class AudioResult:
    path: str
    duration: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "duration": round(self.duration, 3), **self.extra}


class CloudProvider:
    id: ClassVar[str]
    name: ClassVar[str]
    docs_url: ClassVar[str]
    key_url: ClassVar[str] = ""
    default_base_url: ClassVar[str]
    base_url_editable: ClassVar[bool] = False
    options: ClassVar[tuple[OptionInfo, ...]] = ()

    def __init__(self, ctx: ProviderContext) -> None:
        self.ctx = ctx

    # ---------- 配置 ----------

    @property
    def base_url(self) -> str:
        return (self.ctx.base_url or self.default_base_url).rstrip("/")

    def option(self, key: str) -> str:
        value = self.ctx.options.get(key)
        if value:
            return value
        for info in self.options:
            if info.key == key:
                return info.default
        raise KeyError(key)

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.ctx.api_key}"}

    def require_key(self) -> None:
        if not self.ctx.api_key:
            raise ProviderError(f"请先在设置里填写 {self.name} 的 API Key")

    def client(self, timeout: float = 300.0) -> httpx.AsyncClient:
        self.require_key()
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.auth_headers(),
            timeout=httpx.Timeout(timeout, connect=15.0),
            transport=self.ctx.transport,
            follow_redirects=True,
        )

    # ---------- 分发 ----------

    async def run(self, kind: str, op: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if op == "tts.generate":
                if kind == "sfx":
                    return await self.sfx(params)
                if kind == "dialogue":
                    return await self.dialogue(params)
                return await self.tts(params)
            if op == "asr.transcribe":
                return await self.asr(params)
            if op == "music.generate":
                return await self.music(params)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} 请求超时") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"无法连接 {self.name}：{exc}") from exc
        raise ProviderError(f"{self.name} 不支持 {op}（{kind}）")

    async def tts(self, params: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError(f"{self.name} 不支持语音合成")

    async def asr(self, params: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError(f"{self.name} 不支持语音转文字")

    async def sfx(self, params: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError(f"{self.name} 不支持音效生成")

    async def music(self, params: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError(f"{self.name} 不支持音乐生成")

    async def dialogue(self, params: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError(f"{self.name} 不支持多角色音频生成")

    # ---------- 工具 ----------

    def check(self, resp: httpx.Response) -> None:
        """HTTP 出错时抛出带服务商原始信息的错误。"""
        if resp.status_code < 400:
            return
        detail = resp.text[:300]
        try:
            body = resp.json()
            detail = str(
                body.get("message") or body.get("detail") or body.get("error") or body
            )[:300]
        except ValueError:
            pass
        if resp.status_code in (401, 403):
            raise ProviderError(f"{self.name} 拒绝了请求，请检查 API Key（{detail}）")
        if resp.status_code == 402:
            raise ProviderError(f"{self.name} 账户余额不足（{detail}）")
        if resp.status_code == 429:
            raise ProviderError(f"{self.name} 请求太频繁或超出额度（{detail}）")
        raise ProviderError(f"{self.name} 返回错误 HTTP {resp.status_code}：{detail}")

    def save_audio(self, data: bytes, ext: str, output_path: str | Path) -> AudioResult:
        """把服务商返回的音频存成 WAV（统一格式，方便后续混音和质检）。"""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if not data:
            raise ProviderError(f"{self.name} 返回了空音频")
        if ext == "wav":
            out.write_bytes(data)
        else:
            self.ctx.tmp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                suffix=f".{ext}", dir=self.ctx.tmp_dir, delete=False
            ) as tmp:
                tmp.write(data)
            try:
                self.ctx.ffmpeg.to_wav_native(tmp.name, out)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
        return AudioResult(str(out), wav_duration(out))

    async def download(self, client: httpx.AsyncClient, url: str) -> bytes:
        # 下载地址通常在服务商的对象存储上，不需要也不应该带 API Key
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=15.0),
            transport=self.ctx.transport,
            follow_redirects=True,
        ) as plain:
            resp = await plain.get(url)
        self.check(resp)
        return resp.content

    def reference_fingerprint(self, path: str | Path, extra: str = "") -> str:
        h = hashlib.sha256(Path(path).read_bytes())
        h.update(extra.encode())
        return h.hexdigest()[:24]

    def reference_clip(self, path: str | Path, max_seconds: float = 30.0) -> Path:
        """把参考录音裁到服务商允许的长度，转成小体积 mp3。"""
        self.ctx.tmp_dir.mkdir(parents=True, exist_ok=True)
        fp = self.reference_fingerprint(path)
        out = self.ctx.tmp_dir / f"ref-{fp}-{int(max_seconds)}.mp3"
        if not out.exists():
            self.ctx.ffmpeg.to_mp3(path, out, sample_rate=24000, bitrate="96k",
                                   max_seconds=max_seconds)
        return out

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "id": cls.id,
            "name": cls.name,
            "docs_url": cls.docs_url,
            "key_url": cls.key_url,
            "default_base_url": cls.default_base_url,
            "base_url_editable": cls.base_url_editable,
            "options": [
                {"key": o.key, "label": o.label, "default": o.default, "hint": o.hint}
                for o in cls.options
            ],
        }


def words_to_segments(
    words: list[dict[str, Any]], *, max_gap: float = 1.0, max_len: float = 30.0
) -> list[dict[str, Any]]:
    """把逐词结果（带说话人）合并成句段：换人、停顿过长或段落过长时断开。"""
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for w in words:
        text = str(w.get("text", ""))
        if w.get("type") == "spacing" or w.get("start") is None:
            if current is not None:
                current["text"] += text
            continue
        speaker = w.get("speaker_id") or w.get("speaker")
        start, end = float(w["start"]), float(w.get("end", w["start"]))
        if (
            current is None
            or speaker != current["speaker"]
            or start - current["end"] > max_gap
            or end - current["start"] > max_len
        ):
            current = {"start": start, "end": end, "text": text, "speaker": speaker}
            segments.append(current)
        else:
            current["text"] += text
            current["end"] = end
    for seg in segments:
        seg["text"] = seg["text"].strip()
    return [s for s in segments if s["text"]]
