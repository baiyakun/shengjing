"""Intel 芯片的 Mac：不能运行本地模型，所有功能都走在线 API。"""

import json
import sys

import httpx
import pytest

from shengjing_engine.config import DataPaths, RuntimePaths
from shengjing_engine.core import memory
from shengjing_engine.core.memory import LocalSupport
from shengjing_engine.core.recommend import PROVIDER_NAMES, Machine, recommend
from shengjing_engine.core.tasks import UserFacingError
from shengjing_engine.providers import PROVIDERS
from shengjing_engine.services import create_services

from .test_providers import Server, wav_bytes
from .test_workflows import run_task

INTEL = LocalSupport(supported=False, arch="x86_64", apple_silicon=False,
                     reason="这台 Mac 使用 Intel 芯片，不能在本机运行模型，只能使用在线 API")


def intel_services(tmp_path):
    return create_services(DataPaths(tmp_path / "data"), RuntimePaths(sys.executable), "t",
                           local=INTEL)


@pytest.fixture
def detect(monkeypatch):
    """模拟不同的机器来测 local_support()。"""
    monkeypatch.delenv("SHENGJING_LOCAL_MODELS", raising=False)

    def run(*, platform: str, arch: str, arm64_hw: str = "") -> LocalSupport:
        memory.local_support.cache_clear()
        monkeypatch.setattr(memory.sys, "platform", platform)
        monkeypatch.setattr(memory.platform, "machine", lambda: arch)
        monkeypatch.setattr(memory, "_sysctl", lambda name: arm64_hw)
        return memory.local_support()

    yield run
    memory.local_support.cache_clear()


def test_detects_intel_rosetta_and_apple_silicon(detect, monkeypatch):
    assert detect(platform="darwin", arch="arm64").supported
    intel = detect(platform="darwin", arch="x86_64", arm64_hw="0")
    assert not intel.supported and "Intel 芯片" in intel.reason
    rosetta = detect(platform="darwin", arch="x86_64", arm64_hw="1")
    assert not rosetta.supported and rosetta.apple_silicon
    assert "安装 Apple 芯片版本" in rosetta.reason
    # 其他系统只用于开发和测试
    assert detect(platform="linux", arch="x86_64").supported
    monkeypatch.setenv("SHENGJING_LOCAL_MODELS", "0")
    assert not detect(platform="darwin", arch="arm64").supported


def test_provider_names_match_providers():
    assert PROVIDER_NAMES == {pid: cls.name for pid, cls in PROVIDERS.items()}


def test_recommendations_only_use_connected_online_services():
    machine = Machine(total_gb=16, budget_gb=12, chip="Intel(R) Core(TM) i7",
                      local_supported=False)

    def pick(ready=()):
        return recommend(machine, "personal", worker_ready=lambda w: True,
                         provider_ready=lambda p: p in ready)

    recs = pick()
    assert recs["tts"].model is None and not recs["tts"].fits
    assert "在线 API" in recs["tts"].reason and "ElevenLabs" in recs["tts"].reason
    assert recs["llm"].model is None and "云端" in recs["llm"].reason
    # 录音编辑没有在线服务
    assert recs["edit"].model is None and "Apple 芯片" in recs["edit"].reason
    recs = pick(ready=("elevenlabs",))
    assert recs["tts"].model == "elevenlabs-tts" and recs["tts"].fits
    assert recs["asr"].model == "elevenlabs-asr"
    assert recs["music"].model == "elevenlabs-music"
    assert recs["dialogue"].model is None  # 多人对话只有 SenseAudio 提供
    assert pick(ready=("senseaudio",))["dialogue"].model == "senseaudio-audiogen"


def test_engine_disables_local_models(tmp_path):
    services = intel_services(tmp_path)
    for name in services.workers.specs:
        assert not services.workers.available(name)
        assert services.workers.specs[name].unavailable_reason == INTEL.reason
    with pytest.raises(UserFacingError, match="在线 API"):
        services.model_for("tts")
    assert services.llm_provider() == "cloud"
    info = services.system_info()
    assert info["local"]["supported"] is False
    assert info["llm_default"] == "云端（还没设置）"
    models = {m["id"]: m for m in services.model_list()}
    assert models["fish-s2-pro"]["runs_here"] is False
    assert models["fish-s2-pro"]["download"]["state"] == "unsupported"
    assert models["elevenlabs-tts"]["runs_here"] is True
    assert not any(m["default"] for m in models.values() if m["worker"] != "cloud")
    overview = services.downloads.overview()
    assert overview["supported"] is False
    assert services.downloads.refresh_sizes() is False


def test_download_api_refuses_local_models(tmp_path):
    from fastapi.testclient import TestClient

    from shengjing_engine.api.app import create_app

    services = intel_services(tmp_path)
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(services, run_background=False)) as c:
        r = c.post("/downloads/fish-s2-pro", headers=auth)
        assert r.status_code == 400 and "Apple 芯片" in r.json()["detail"]
        assert c.get("/system", headers=auth).json()["local"]["supported"] is False


async def test_local_llm_setting_is_ignored(tmp_path):
    from shengjing_engine.workflows.common import llm_session

    services = intel_services(tmp_path)
    services.settings.update({"llm": {"provider": "local"}})
    with pytest.raises(UserFacingError, match="云端 LLM"):
        async with llm_session(services):
            pass
    services.settings.update({"llm": {"provider": "local", "cloud_base_url": "https://api.x/v1",
                                      "cloud_model": "deepseek-chat"}})
    async with llm_session(services) as client:
        assert client.model == "deepseek-chat"
    assert services.system_info()["llm_default"] == "云端 · deepseek-chat"


async def test_tts_runs_through_the_connected_online_service(tmp_path):
    services = intel_services(tmp_path)
    server = Server({"POST /v1/audio/speech": lambda req: httpx.Response(200, content=wav_bytes())})
    original = services.provider_context
    services.provider_context = lambda pid, **kw: original(
        pid, transport=httpx.MockTransport(server), **kw)

    failed = await run_task(services, "tts.quick", {"text": "你好"})
    assert failed["status"] == "failed" and "在线 API" in failed["error"]

    services.secrets.set("openai_api_key", "k")
    task = await run_task(services, "tts.quick", {"text": "你好"})
    assert task["status"] == "succeeded", task["error"]
    assert json.loads(server.calls[-1].content)["input"] == "你好"

    # 指定本地模型时给出能看懂的原因
    local = await run_task(services, "tts.quick", {"text": "你好", "model": "fish-s2-pro"})
    assert local["status"] == "failed" and "Intel 芯片" in local["error"]
