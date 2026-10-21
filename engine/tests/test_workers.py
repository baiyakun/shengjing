"""用真实子进程验证：启动、就绪检测、加载/运行、关闭、意外退出、内存调度。"""

import asyncio
import sys
from pathlib import Path

import pytest

from shengjing_engine.core.scheduler import ModelScheduler
from shengjing_engine.core.workers import WorkerError, WorkerManager, WorkerSpec, WorkerUnavailable

FAKE = str(Path(__file__).with_name("fake_kit_worker.py"))


def manager(tmp_path) -> WorkerManager:
    def cmd(port, token, source):
        return [sys.executable, FAKE, "--port", str(port)]

    return WorkerManager(
        [
            WorkerSpec(name="speech", protocol="kit", command=cmd, startup_timeout=20),
            WorkerSpec(name="analysis", protocol="kit", command=None, unavailable_reason="未安装"),
        ],
        logs_dir=tmp_path,
        token="secret",
    )


async def test_start_run_stop(tmp_path):
    m = manager(tmp_path)
    proc = await m.start("speech")
    assert proc.alive and proc.port
    await proc.kit.load("qwen3-asr-1.7b", "src", {})
    assert await proc.kit.run("echo", "qwen3-asr-1.7b", {"x": 1}) == {"echo": {"x": 1}}
    with pytest.raises(WorkerError, match="not loaded"):
        await proc.kit.run("echo", "other", {})
    await m.stop("speech")
    assert m.get("speech") is None
    assert "some import noise" in (tmp_path / "worker-speech.log").read_text()


async def test_unavailable_worker(tmp_path):
    m = manager(tmp_path)
    assert not m.available("analysis")
    with pytest.raises(WorkerUnavailable, match="未安装"):
        await m.start("analysis")


async def test_scheduler_loads_and_recovers_from_crash(tmp_path):
    m = manager(tmp_path)
    s = ModelScheduler(m, budget_gb=20)
    try:
        async with s.use("qwen3-asr-1.7b") as h:
            assert await h.run("echo", {"a": 1}) == {"echo": {"a": 1}}
        assert s.status()["workers"][0]["loaded"] == ["qwen3-asr-1.7b"]

        with pytest.raises(Exception):  # noqa: B017 - 进程崩溃时连接被断开
            async with s.use("qwen3-asr-1.7b") as h:
                await h.run("crash", {})
        for _ in range(50):
            if not s.planner.workers["speech"].running:
                break
            await asyncio.sleep(0.1)
        assert not s.planner.workers["speech"].running

        # 再次使用时自动重新启动并加载
        async with s.use("qwen3-asr-1.7b") as h:
            assert await h.run("echo", {}) == {"echo": {}}
    finally:
        await m.stop_all()


async def test_stop_idle(tmp_path):
    m = manager(tmp_path)
    s = ModelScheduler(m, budget_gb=20)
    try:
        async with s.use("qwen3-asr-1.7b"):
            assert await s.stop_idle(0) == []  # 使用中不能关
        assert await s.stop_idle(0) == ["speech"]
        assert m.get("speech") is None
    finally:
        await m.stop_all()
