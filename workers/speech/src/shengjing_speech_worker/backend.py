"""mlx-speech 适配。

支持的操作：
- tts.generate     文字 → 音频文件（TTS、克隆、音效、Step-Audio 编辑都走这里）
- asr.transcribe   音频文件 → 文字
- asr.stream_open / asr.stream_feed / asr.stream_close   实时转写会话

参数直接透传给 mlx-speech 的 generate()；各模型会忽略自己不认识的参数
（mlx-speech 的 TTSModel 协议约定）。
"""

from __future__ import annotations

import base64
import gc
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from shengjing_worker_kit import BackendError

_TTS_LOAD_KEYS = ("artifact_subdir", "codec_path_or_repo", "gemma_path_or_repo", "revision")
_ASR_LOAD_KEYS = ("revision",)
_REF_CACHE_SIZE = 32


def _mx():
    import mlx.core as mx

    return mx


class SpeechBackend:
    name = "speech"

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._kinds: dict[str, str] = {}
        self._refs: OrderedDict[tuple, Any] = OrderedDict()
        self._streams: dict[str, tuple[str, Any]] = {}

    # ---------- 生命周期 ----------

    def load(self, model: str, source: str, options: dict[str, Any]) -> None:
        if model in self._models:
            return
        import mlx_speech

        kind = options.get("kind", "tts")
        if kind == "asr":
            kwargs = {k: options[k] for k in _ASR_LOAD_KEYS if options.get(k)}
            obj = mlx_speech.asr.load(source, **kwargs)
        elif kind == "tts":
            kwargs = {k: options[k] for k in _TTS_LOAD_KEYS if options.get(k)}
            obj = mlx_speech.tts.load(source, **kwargs)
        else:
            raise BackendError(f"未知模型类型：{kind}")
        self._models[model] = obj
        self._kinds[model] = kind

    def unload(self, model: str) -> None:
        self._models.pop(model, None)
        self._kinds.pop(model, None)
        for key in [k for k in self._refs if k[0] == model]:
            del self._refs[key]
        for sid in [s for s, (m, _) in self._streams.items() if m == model]:
            del self._streams[sid]
        gc.collect()
        try:
            mx = _mx()
            clear = getattr(mx, "clear_cache", None) or getattr(mx.metal, "clear_cache", None)
            if clear:
                clear()
        except ImportError:
            pass

    def loaded(self) -> list[str]:
        return sorted(self._models)

    def memory(self) -> dict[str, Any]:
        try:
            mx = _mx()
        except ImportError:
            return {}
        out = {}
        for key in ("get_active_memory", "get_peak_memory", "get_cache_memory"):
            fn = getattr(mx, key, None) or getattr(getattr(mx, "metal", None), key, None)
            if fn:
                out[key.removeprefix("get_") + "_gb"] = round(fn() / 1024**3, 3)
        return out

    # ---------- 运行 ----------

    def run(self, op: str, model: str, params: dict[str, Any]) -> dict[str, Any]:
        obj = self._models.get(model)
        if obj is None:
            raise BackendError(f"模型 {model} 没有加载")
        if op == "tts.generate":
            self._expect(model, "tts", op)
            return self._tts(model, obj, params)
        if op == "asr.transcribe":
            self._expect(model, "asr", op)
            return self._asr(obj, params)
        if op == "asr.stream_open":
            self._expect(model, "asr", op)
            return self._stream_open(model, obj, params)
        if op == "asr.stream_feed":
            return self._stream_feed(params)
        if op == "asr.stream_close":
            return self._stream_close(params)
        raise BackendError(f"不支持的操作：{op}")

    def _expect(self, model: str, kind: str, op: str) -> None:
        if self._kinds.get(model) != kind:
            raise BackendError(f"{model} 不支持 {op}")

    def _prepared(self, model_id: str, obj: Any, path: str, text: str) -> Any:
        """Fish S2 Pro、Drama 3 支持预编码参考录音；同一个声音反复使用时只编码一次。"""
        p = Path(path)
        key = (model_id, str(p), p.stat().st_mtime_ns if p.exists() else 0, text)
        if key in self._refs:
            self._refs.move_to_end(key)
            return self._refs[key]
        prepared = obj.prepare_reference(str(p), reference_text=text)
        self._refs[key] = prepared
        while len(self._refs) > _REF_CACHE_SIZE:
            self._refs.popitem(last=False)
        return prepared

    def _tts(self, model_id: str, obj: Any, params: dict[str, Any]) -> dict[str, Any]:
        out = params.pop("output_path", None)
        if not out:
            raise BackendError("缺少 output_path")
        text = params.pop("text", None)
        kwargs = {k: v for k, v in params.items() if v is not None}
        ref = kwargs.get("reference_audio")
        ref_text = kwargs.get("reference_text")
        if isinstance(ref, str) and isinstance(ref_text, str) and hasattr(obj, "prepare_reference"):
            kwargs["reference_audio"] = self._prepared(model_id, obj, ref, ref_text)
            kwargs.pop("reference_text")
        result = obj.generate(text, **kwargs)
        return _write(result, Path(out))

    def _asr(self, obj: Any, params: dict[str, Any]) -> dict[str, Any]:
        audio = params.get("audio_path")
        if not audio:
            raise BackendError("缺少 audio_path")
        kwargs = {"language": params["language"]} if params.get("language") else {}
        result = obj.generate(str(audio), **kwargs)
        return {"text": result.text, "language": getattr(result, "language", None)}

    def _stream_open(self, model_id: str, obj: Any, params: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(obj, "stream_session"):
            raise BackendError(f"{model_id} 不支持实时转写")
        kwargs = {"language": params["language"]} if params.get("language") else {}
        session = obj.stream_session(**kwargs)
        sid = uuid.uuid4().hex
        self._streams[sid] = (model_id, session)
        return {"session_id": sid}

    def _session(self, params: dict[str, Any]) -> Any:
        entry = self._streams.get(params.get("session_id") or "")
        if entry is None:
            raise BackendError("实时转写会话不存在或已结束")
        return entry[1]

    def _stream_feed(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params)
        raw = base64.b64decode(params.get("pcm_f32_b64") or "")
        pcm = np.frombuffer(raw, dtype="<f4").astype(np.float32)
        update = session.feed(pcm)
        # Confucius4 返回 committed（只增不改）和 hypothesis（可能变化）
        if hasattr(update, "committed"):
            return {"committed": update.committed, "hypothesis": update.hypothesis,
                    "delta": getattr(update, "delta", "")}
        return {}

    def _stream_close(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params)
        del self._streams[params["session_id"]]
        final = session.finalize()
        if hasattr(final, "text"):
            return {"text": final.text, "language": getattr(final, "language", None)}
        # Nemotron：finalize 返回 token，文字在 result() 里
        if hasattr(session, "result"):
            res = session.result()
            return {"text": res.text, "language": getattr(res, "language", None)}
        return {"text": ""}


def _write(result: Any, out: Path) -> dict[str, Any]:
    from mlx_speech.audio import write_wav

    waveform = result.waveform
    sample_rate = int(result.sample_rate)
    write_wav(out, waveform, sample_rate=sample_rate)
    shape = tuple(int(x) for x in getattr(waveform, "shape", ()))
    frames = shape[-1] if shape else 0
    channels = shape[0] if len(shape) == 2 else 1
    return {
        "path": str(out),
        "sample_rate": sample_rate,
        "channels": channels,
        "duration": round(frames / sample_rate, 3) if sample_rate else 0.0,
    }
