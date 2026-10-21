"""在线服务商。

在线 API 和本地模型一样登记在模型目录里（worker="cloud"，source 为服务商 ID），
调度器遇到它们时不启动本地进程，而是返回 CloudHandle。CloudHandle 和本地模型的
ModelHandle 接口相同（run(op, params)），所以工作流不需要区分本地和在线。
"""

from __future__ import annotations

from typing import Any

from ..core.catalog import ModelSpec
from .base import CloudProvider, ProviderContext, ProviderError
from .elevenlabs import ElevenLabsProvider
from .fish_audio import FishAudioProvider
from .openai_compat import OpenAICompatProvider
from .senseaudio import SenseAudioProvider

PROVIDERS: dict[str, type[CloudProvider]] = {
    cls.id: cls
    for cls in (OpenAICompatProvider, SenseAudioProvider, FishAudioProvider, ElevenLabsProvider)
}

__all__ = ["PROVIDERS", "CloudHandle", "CloudProvider", "ProviderContext", "ProviderError"]


class CloudHandle:
    def __init__(self, spec: ModelSpec, provider: CloudProvider) -> None:
        self.spec = spec
        self.model_id = spec.id
        self.provider = provider

    async def run(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self.provider.run(self.spec.kind, op, dict(params))
