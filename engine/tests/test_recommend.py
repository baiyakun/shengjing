from shengjing_engine.core import catalog
from shengjing_engine.core.memory import MemoryBudget
from shengjing_engine.core.recommend import Machine, fits, recommend


def mac(total_gb: float, chip: str = "Apple M3 Pro") -> Machine:
    budget = MemoryBudget(total_gb=total_gb, reserve_gb=max(4.0, total_gb * 0.2))
    return Machine(total_gb=total_gb, budget_gb=budget.budget_gb, chip=chip)


def pick(machine: Machine, edition="personal", *, ready=(), missing=(), qa=True,
         in_use=None) -> dict[str, str]:
    recs = recommend(machine, edition, worker_ready=lambda w: w not in missing,
                     provider_ready=lambda p: p in ready, qa_enabled=qa, in_use=in_use)
    return {k: r.model for k, r in recs.items()}


def test_chip_class():
    assert mac(16, "Apple M1").chip_class == "base"
    assert mac(16, "Apple M2 Pro").chip_class == "pro"
    assert mac(16, "Apple M4 Max").chip_class == "max"
    assert mac(16, "unknown").chip_class is None


def test_tts_fits_with_qa_transcriber():
    # 16GB 放不下 Fish S2 Pro + 质检用的转写模型，换成更小的 dots.tts
    assert pick(mac(16))["tts"] == "dots-tts-soar"
    assert pick(mac(16, "Apple M2"))["tts"] == "dots-tts-mf"  # 基础款芯片用更快的版本
    # 关掉质检回听就不用同时放下转写模型
    assert pick(mac(16), qa=False)["tts"] == "fish-s2-pro"
    assert pick(mac(24))["tts"] == "fish-s2-pro"
    # 转写改用在线服务，也不用和本地转写模型挤在一起
    assert pick(mac(16), in_use={"asr": "elevenlabs-asr"})["tts"] == "fish-s2-pro"
    # 实时转写：LLM 改用云端后，本地只放转写模型
    assert pick(mac(64), in_use={"llm": ""})["asr_stream"] == "confucius4-r2t2"


def test_llm_uses_at_most_half_of_model_memory():
    assert pick(mac(16))["llm"] == "qwen3-4b-4bit"
    assert pick(mac(24))["llm"] == "qwen3.5-9b-4bit"
    assert pick(mac(32))["llm"] == "qwen3.5-9b-4bit"
    assert pick(mac(64))["llm"] == "qwen3.6-35b-a3b-4bit"


def test_dialogue_needs_more_memory():
    recs = recommend(mac(16), "personal", worker_ready=lambda w: True,
                     provider_ready=lambda p: False)
    assert recs["dialogue"].fits is False
    assert pick(mac(16), ready=("senseaudio",))["dialogue"] == "senseaudio-audiogen"
    assert pick(mac(32))["dialogue"] == "moss-ttsd"


def test_commercial_edition_prefers_plain_commercial_licenses():
    chosen = pick(mac(64), "commercial")
    for model_id in chosen.values():
        if model_id:
            assert catalog.get(model_id).commercial != "no"
    assert chosen["tts"] == "dots-tts-soar"
    assert chosen["asr_stream"] == "nemotron-asr-streaming"
    assert chosen["voice_design"] is None
    assert pick(mac(64))["asr_stream"] == "confucius4-r2t2"


def test_cloud_when_local_cannot_run():
    assert pick(mac(16))["music"] == "ace-step-1.5"
    assert pick(mac(16), ready=("elevenlabs",))["music"] == "elevenlabs-music"
    # 本机放得下，但扩展包没装、在线服务已连接：先用在线服务
    assert pick(mac(32), ready=("elevenlabs",), missing=("music",))["music"] == "elevenlabs-music"
    assert pick(mac(32), ready=("elevenlabs",))["music"] == "ace-step-1.5"


def test_models_needing_conversion_are_never_recommended():
    chosen = pick(mac(192))
    assert chosen["dialogue"] == "moss-ttsd"
    assert all(not catalog.get(m).needs_conversion for k, m in chosen.items()
               if m and k != "voice_design")


def test_fits():
    assert fits(catalog.get("fish-drama-3"), mac(64))
    assert not fits(catalog.get("fish-drama-3"), mac(32))
    assert fits(catalog.get("elevenlabs-tts"), mac(8))


def test_services_follow_overrides_then_recommendations(services):
    # conftest 把内存设成 32GB
    assert services.model_for("tts").id == "fish-s2-pro"
    services.settings.update({"model_overrides": {"tts": "longcat"}})
    assert services.model_for("tts").id == "longcat"
    models = {m["id"]: m for m in services.model_list()}
    assert models["longcat"]["default"] and not models["longcat"]["recommended"]
    assert models["fish-s2-pro"]["recommended"] and not models["fish-s2-pro"]["default"]
    assert models["fish-drama-3"]["fits"] is False
    info = services.system_info()
    assert info["recommendations"]["tts"]["model"] == "fish-s2-pro"
    assert info["llm_default"] == "qwen3.5-9b-4bit"
