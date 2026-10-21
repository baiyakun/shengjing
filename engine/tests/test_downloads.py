import asyncio
import sys
from pathlib import Path

import pytest

from shengjing_engine.core import catalog
from shengjing_engine.core.downloads import (
    ARTIFACTS,
    DownloadError,
    DownloadManager,
    repo_cache_dir,
)

FAKE = [sys.executable, str(Path(__file__).with_name("fake_downloader.py"))]


def manager(tmp_path: Path, events: list | None = None, music: str | None = None):
    hf_home = tmp_path / "models"
    hf_home.mkdir(parents=True, exist_ok=True)
    return DownloadManager(
        hf_home,
        hub_env=lambda: {"HF_HOME": str(hf_home)},
        music_python=lambda: music,
        publish=(events.append if events is not None else lambda e: None),
        command=FAKE,
    )


async def run_until_idle(m: DownloadManager, model_id: str, timeout: float = 20) -> None:
    runner = asyncio.create_task(m.run_forever())
    try:
        for _ in range(int(timeout / 0.05)):
            if not m.jobs[model_id].active:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("下载没有结束")
    finally:
        runner.cancel()


async def test_download_writes_record_and_reports_progress(tmp_path):
    events: list = []
    m = manager(tmp_path, events)
    assert m.local_state("fish-s2-pro")[0] == "missing"
    m.start("fish-s2-pro")
    await run_until_idle(m, "fish-s2-pro")
    job = m.jobs["fish-s2-pro"]
    assert job.state == "done", job.error
    state, size, _ = m.local_state("fish-s2-pro")
    assert state == "ready" and size == 512
    kinds = [e["download"]["state"] for e in events if e["type"] == "download"]
    assert kinds[0] == "queued" and "running" in kinds and kinds[-1] == "done"
    with pytest.raises(DownloadError):
        m.start("fish-s2-pro")  # 已经下载好了
    # 文件被删掉后不再算“已下载”
    snap = repo_cache_dir(m.hf_home, "appautomaton/fishaudio-s2-pro-8bit-mlx") / "snapshots"
    next(snap.iterdir()).joinpath("config.json").unlink()
    assert m.local_state("fish-s2-pro")[0] == "partial"


async def test_states_for_models_that_are_not_downloaded_here(tmp_path):
    m = manager(tmp_path)
    assert m.local_state("elevenlabs-tts")[0] == "unmanaged"
    assert m.local_state("fish-drama-3")[0] == "conversion"
    assert m.local_state("htdemucs")[0] == "unmanaged"
    assert m.local_state("ace-step-1.5")[0] == "unavailable"  # 音乐扩展包没装
    with pytest.raises(DownloadError):
        m.start("fish-drama-3")
    assert manager(tmp_path, music="/x/python").local_state("ace-step-1.5")[0] == "missing"


async def test_shared_repo_variants_do_not_look_partial(tmp_path):
    m = manager(tmp_path)
    m.start("dots-tts-soar")
    await run_until_idle(m, "dots-tts-soar")
    assert m.local_state("dots-tts-soar")[0] == "ready"
    # 同一个仓库里的另一个版本还没下载
    assert m.local_state("dots-tts-mf")[0] == "missing"


async def test_failed_download_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DL_MODE", "auth")
    m = manager(tmp_path)
    m.start("pyannote-community-1")
    await run_until_idle(m, "pyannote-community-1")
    job = m.jobs["pyannote-community-1"]
    assert job.state == "failed" and job.error_kind == "auth"
    assert "同意使用条款" in job.error


async def test_cancel_stops_the_download_process(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DL_MODE", "slow")
    m = manager(tmp_path)
    m.start("qwen3-asr-1.7b")
    runner = asyncio.create_task(m.run_forever())
    try:
        for _ in range(200):
            if m.jobs["qwen3-asr-1.7b"].done_bytes:
                break
            await asyncio.sleep(0.05)
        m.cancel("qwen3-asr-1.7b")
        assert m.jobs["qwen3-asr-1.7b"].state == "cancelled"
        for _ in range(100):
            if m._proc is None:
                break
            await asyncio.sleep(0.05)
        assert m._proc is None  # 子进程已经结束
    finally:
        runner.cancel()


async def test_ensure_downloads_before_use_and_reports_progress(tmp_path):
    m = manager(tmp_path)
    runner = asyncio.create_task(m.run_forever())
    seen: list[str] = []
    try:
        await asyncio.wait_for(
            m.ensure(catalog.get("qwen3-asr-1.7b"), on_progress=lambda j: seen.append(j.state)),
            20,
        )
    finally:
        runner.cancel()
    assert m.local_state("qwen3-asr-1.7b")[0] == "ready"
    assert seen[0] == "queued" and seen[-1] == "done"
    # 已经下载好的直接返回；在线模型不用下载
    await m.ensure(catalog.get("qwen3-asr-1.7b"))
    await m.ensure(catalog.get("elevenlabs-tts"))


async def test_ensure_raises_when_download_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DL_MODE", "auth")
    m = manager(tmp_path)
    runner = asyncio.create_task(m.run_forever())
    try:
        with pytest.raises(DownloadError, match="下载失败"):
            await asyncio.wait_for(m.ensure(catalog.get("qwen3-asr-1.7b")), 20)
        # 本地已有以前下载的文件：放行，交给模型进程
        (repo_cache_dir(m.hf_home, "appautomaton/cohere-asr-mlx") / "snapshots").mkdir(
            parents=True)
        await asyncio.wait_for(m.ensure(catalog.get("cohere-asr")), 20)
    finally:
        runner.cancel()


async def test_delete_keeps_files_shared_with_other_models(tmp_path):
    m = manager(tmp_path)
    runner = asyncio.create_task(m.run_forever())
    try:
        for model in ("moss-ttsd", "moss-local"):
            m.start(model)
        for _ in range(400):
            if not any(j.active for j in m.jobs.values()):
                break
            await asyncio.sleep(0.05)
    finally:
        runner.cancel()
    assert m.local_state("moss-ttsd")[0] == m.local_state("moss-local")[0] == "ready"
    codec = ARTIFACTS["moss-ttsd"][1].repo_id
    m.delete("moss-ttsd")
    assert m.local_state("moss-ttsd")[0] == "missing"
    # 共用的音频编解码器还在，moss-local 仍然可用
    assert m.local_state("moss-local")[0] == "ready"
    assert repo_cache_dir(m.hf_home, codec).exists()
    assert not repo_cache_dir(m.hf_home, ARTIFACTS["moss-ttsd"][0].repo_id).exists()
    m.delete("moss-local")
    assert not repo_cache_dir(m.hf_home, codec).exists()


async def test_delete_one_variant_of_a_shared_repo(tmp_path):
    m = manager(tmp_path)
    runner = asyncio.create_task(m.run_forever())
    try:
        for model in ("dots-tts-soar", "dots-tts-mf"):
            m.start(model)
        for _ in range(400):
            if not any(j.active for j in m.jobs.values()):
                break
            await asyncio.sleep(0.05)
    finally:
        runner.cancel()
    m.delete("dots-tts-soar")
    assert m.local_state("dots-tts-soar")[0] == "missing"
    assert m.local_state("dots-tts-mf")[0] == "ready"


async def test_sizes_are_fetched_and_cached(tmp_path):
    events: list = []
    m = manager(tmp_path, events)
    assert m.refresh_sizes() is True
    await asyncio.wait_for(m._sizes_task, 20)
    assert m.sizes()["fish-s2-pro"] == 2048
    assert m.refresh_sizes() is False  # 一天内不重复查询
    assert {"type": "download_sizes"} in events
    assert m.status("fish-s2-pro")["remote_bytes"] == 2048


def test_artifacts_cover_every_downloadable_local_model():
    for spec in catalog.all_models():
        if spec.worker == "cloud" or spec.needs_conversion or spec.id in ("htdemucs", "silero-vad"):
            continue
        assert spec.id in ARTIFACTS, spec.id
    for model_id in ARTIFACTS:
        catalog.get(model_id)


def test_llm_artifacts_match_catalog_sources():
    for spec in catalog.all_models():
        if spec.worker == "llm":
            assert ARTIFACTS[spec.id][0].repo_id == spec.source


def test_download_api(services):
    from fastapi.testclient import TestClient

    from shengjing_engine.api.app import create_app

    auth = {"Authorization": "Bearer test-token"}
    services.downloads._command = FAKE
    with TestClient(create_app(services, run_background=False)) as c:
        overview = c.get("/downloads", headers=auth).json()
        assert overview["models"]["fish-s2-pro"]["state"] == "missing"
        assert "elevenlabs-tts" not in overview["models"]
        assert c.post("/downloads/nope", headers=auth).status_code == 404
        assert c.post("/downloads/fish-drama-3", headers=auth).status_code == 400
        job = c.post("/downloads/fish-s2-pro", headers=auth).json()
        assert job["state"] == "queued"
        assert c.post("/downloads/fish-s2-pro/cancel", headers=auth).json()["state"] == "cancelled"
        models = {m["id"]: m for m in c.get("/catalog", headers=auth).json()}
        assert models["fish-s2-pro"]["download"]["state"] == "missing"
        assert models["elevenlabs-tts"]["download"] is None


def test_download_settings_reach_new_worker_processes(services):
    services.settings.update({"hf_endpoint": "https://hf-mirror.com/"})
    services.secrets.set("hf_token", "hf_x")
    services.refresh_hub_env()
    env = services.workers._env
    assert env["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert env["HF_TOKEN"] == "hf_x"
    assert env["HF_HOME"] == str(services.paths.hf_home)
    music = services.workers.specs["music"]
    assert music.env["ACESTEP_CHECKPOINTS_DIR"] == str(services.paths.hf_home / "acestep")


async def test_task_shows_download_progress(services):
    from shengjing_engine.core.tasks import CURRENT_TASK

    services.downloads._command = FAKE
    notes: list[str] = []

    class Ctx:
        def note(self, message: str) -> None:
            notes.append(message)

        def check_cancelled(self) -> None:
            pass

    runner = asyncio.create_task(services.downloads.run_forever())
    token = CURRENT_TASK.set(Ctx())  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(services.prepare_model(catalog.get("qwen3-asr-1.7b")), 20)
    finally:
        CURRENT_TASK.reset(token)
        runner.cancel()
    assert notes[0].startswith("等待下载")
    assert any("下载 Qwen3-ASR" in n and "%" in n for n in notes)


async def test_model_needed_by_a_task_jumps_the_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DL_MODE", "slow")
    m = manager(tmp_path)
    for model in ("fish-s2-pro", "moss-ttsd", "qwen3-asr-1.7b"):
        m.start(model)
    m.prioritize("qwen3-asr-1.7b")
    assert m._pending == ["qwen3-asr-1.7b", "fish-s2-pro", "moss-ttsd"]
    m.cancel("moss-ttsd")
    assert m._pending == ["qwen3-asr-1.7b", "fish-s2-pro"]
