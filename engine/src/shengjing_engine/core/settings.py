"""用户设置与密钥。

普通设置保存在数据目录的 settings.json；密钥（云端 LLM 的 API Key、
Hugging Face 访问令牌）保存在系统钥匙串里，不写进文件。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# 在线服务商的 API Key 命名为 <服务商 ID>_api_key
PROVIDER_IDS = ("openai", "senseaudio", "fish_audio", "elevenlabs")
SECRET_NAMES = ("llm_api_key", "hf_token", *(f"{p}_api_key" for p in PROVIDER_IDS))
KEYRING_SERVICE = "dev.shengjing.desktop"


class LLMSettings(BaseModel):
    provider: Literal["local", "cloud"] = "local"
    local_model: str | None = None
    """None 表示按机器内存自动选择。"""
    cloud_base_url: str = ""
    """任何 OpenAI 兼容接口，例如 https://api.deepseek.com"""
    cloud_model: str = ""
    cloud_thinking: bool = False
    """深度思考（DeepSeek 等支持的服务）：先思考再回答，更稳但更慢、费用更高。"""
    temperature: float = 0.7


class ProviderSettings(BaseModel):
    base_url: str = ""
    """留空使用服务商的默认地址。"""
    options: dict[str, str] = Field(default_factory=dict)
    """默认模型、默认声音等，留空的项使用服务商的默认值。"""


class Settings(BaseModel):
    edition: Literal["personal", "commercial"] = "personal"
    llm: LLMSettings = Field(default_factory=LLMSettings)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    """任务类型 → 模型 ID，覆盖默认模型（可以选本地模型，也可以选在线 API）。"""
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    """在线服务商的设置，键为服务商 ID。API Key 存在系统钥匙串里。"""
    hf_endpoint: str = ""
    """模型下载源，例如镜像站 https://hf-mirror.com；留空使用官方地址。"""
    memory_reserve_gb: float | None = None
    idle_timeout_s: float = 600.0
    qa_enabled: bool = True
    qa_max_retries: int = 2
    export_loudness_lufs: float = -16.0


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._settings = self._read()

    def _read(self) -> Settings:
        if self._path.exists():
            try:
                return Settings.model_validate_json(self._path.read_text(encoding="utf-8"))
            except ValueError:
                log.exception("settings.json 无法解析，使用默认设置")
        return Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, patch: dict) -> Settings:
        with self._lock:
            merged = _deep_merge(self._settings.model_dump(), patch)
            self._settings = Settings.model_validate(merged)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(self._settings.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(self._path)
            return self._settings.model_copy(deep=True)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class SecretStore:
    """优先用系统钥匙串（macOS Keychain）；不可用时退回进程内存，仅供开发测试。"""

    def __init__(self) -> None:
        self._memory: dict[str, str] = {}
        self._use_keyring = os.environ.get("SHENGJING_SECRETS") != "memory"
        if self._use_keyring:
            try:
                import keyring
                from keyring.backends.fail import Keyring as FailKeyring

                if isinstance(keyring.get_keyring(), FailKeyring):
                    self._use_keyring = False
            except Exception:  # noqa: BLE001 - 任何导入或后端错误都退回内存
                self._use_keyring = False
        if not self._use_keyring:
            log.warning("系统钥匙串不可用，密钥只保存在内存中")

    def get(self, name: str) -> str | None:
        self._check(name)
        if self._use_keyring:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, name)
        return self._memory.get(name)

    def set(self, name: str, value: str | None) -> None:
        self._check(name)
        if self._use_keyring:
            import keyring

            if value:
                keyring.set_password(KEYRING_SERVICE, name, value)
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE, name)
                except keyring.errors.PasswordDeleteError:
                    pass
            return
        if value:
            self._memory[name] = value
        else:
            self._memory.pop(name, None)

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    @staticmethod
    def _check(name: str) -> None:
        if name not in SECRET_NAMES:
            raise KeyError(f"未知密钥：{name}")
