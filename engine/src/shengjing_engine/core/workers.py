"""模型进程管理：启动、就绪检测、关闭、意外退出通知。

三种进程协议：
- kit：我们自己的工作进程（语音、分析），启动后在标准输出打印一行
  {"event": "ready", "port": N}，接口见 workers/worker-kit。
- openai：mlx-lm 自带的 OpenAI 兼容服务（LLM 进程）。
- acestep：ACE-Step 自带的 API 服务（音乐进程）。
后两种通过轮询健康检查地址判断就绪。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

Protocol = Literal["kit", "openai", "acestep"]
CommandBuilder = Callable[[int, str, str | None], Sequence[str]]
"""(port, token, model_source) → 启动命令。kit 进程的 port 固定传 0。"""


class WorkerError(RuntimeError):
    pass


class WorkerUnavailable(WorkerError):
    """对应的运行环境没有安装（通常是扩展包没装）。"""


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    protocol: Protocol
    command: CommandBuilder | None
    """None 表示这个进程的运行环境没有安装。"""
    single_model: bool = False
    health_path: str = "/health"
    startup_timeout: float = 300.0
    env: dict[str, str] = field(default_factory=dict)
    unavailable_reason: str = ""
    port_env: str | None = None
    """有些服务（ACE-Step）从环境变量读端口，而不是命令行参数。"""
    token_env: str | None = None
    """把引擎令牌作为该服务的 API Key 传入，只有引擎能调用它。"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class KitClient:
    """kit 协议客户端。"""

    def __init__(self, base_url: str, token: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(connect=5.0, read=None, write=60.0, pool=None),
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._http.post(path, json=payload)
        data = _json_or_error(resp)
        if not data.get("ok", False):
            err = data.get("error") or {}
            raise WorkerError(err.get("message") or f"{path} 失败：HTTP {resp.status_code}")
        return data

    async def health(self) -> dict[str, Any]:
        resp = await self._http.get("/health", timeout=5.0)
        return _json_or_error(resp)

    async def load(self, model: str, source: str, options: dict[str, Any]) -> None:
        await self._post("/load", {"model": model, "source": source, "options": options})

    async def unload(self, model: str) -> None:
        await self._post("/unload", {"model": model})

    async def run(self, op: str, model: str, params: dict[str, Any]) -> dict[str, Any]:
        data = await self._post("/run", {"op": op, "model": model, "params": params})
        return data.get("result") or {}

    async def shutdown(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            await self._http.post("/shutdown", json={}, timeout=5.0)

    async def aclose(self) -> None:
        await self._http.aclose()


def _json_or_error(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise WorkerError(f"进程返回了无法解析的内容：HTTP {resp.status_code}") from exc
    if not isinstance(data, dict):
        raise WorkerError("进程返回格式错误")
    return data


class WorkerProcess:
    def __init__(self, spec: WorkerSpec, token: str, log_path: Path, env: dict[str, str]) -> None:
        self.spec = spec
        self.token = token
        self.log_path = log_path
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.model_source: str | None = None
        self.started_at: float | None = None
        self.kit: KitClient | None = None
        self._drain_task: asyncio.Task | None = None
        self._log_file = None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise WorkerError(f"{self.spec.name} 进程还没有就绪")
        return f"http://127.0.0.1:{self.port}"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, model_source: str | None = None) -> None:
        if self.spec.command is None:
            raise WorkerUnavailable(self.spec.unavailable_reason or f"{self.spec.name} 未安装")
        port = 0 if self.spec.protocol == "kit" else free_port()
        argv = list(self.spec.command(port, self.token, model_source))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_path, "ab")  # noqa: SIM115 - 进程存活期间一直写
        self._log_file.write(f"\n===== start {time.strftime('%F %T')} {argv}\n".encode())
        self._log_file.flush()
        env = {**os.environ, **self.env, "SHENGJING_WORKER_TOKEN": self.token}
        env.setdefault("PYTHONUNBUFFERED", "1")
        if self.spec.port_env:
            env[self.spec.port_env] = str(port)
        if self.spec.token_env:
            env[self.spec.token_env] = self.token
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._log_file,
            env=env,
        )
        self.model_source = model_source
        try:
            if self.spec.protocol == "kit":
                self.port = await asyncio.wait_for(
                    self._read_ready_line(), timeout=self.spec.startup_timeout
                )
            else:
                self.port = port
                self._drain_task = asyncio.create_task(self._drain_stdout())
                await asyncio.wait_for(self._poll_health(), timeout=self.spec.startup_timeout)
        except BaseException:
            await self._kill()
            raise
        self.started_at = time.monotonic()
        if self.spec.protocol == "kit":
            self.kit = KitClient(self.base_url, self.token)
        log.info("worker %s ready on port %s", self.spec.name, self.port)

    async def _read_ready_line(self) -> int:
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                code = await self.proc.wait()
                raise WorkerError(
                    f"{self.spec.name} 进程启动失败（退出码 {code}），详情见 {self.log_path}"
                )
            self._write_log(line)
            text = line.decode(errors="replace").strip()
            if text.startswith("{"):
                with contextlib.suppress(ValueError):
                    event = json.loads(text)
                    if event.get("event") == "ready":
                        self._drain_task = asyncio.create_task(self._drain_stdout())
                        return int(event["port"])

    async def _poll_health(self) -> None:
        url = f"http://127.0.0.1:{self.port}{self.spec.health_path}"
        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                if self.proc is not None and self.proc.returncode is not None:
                    raise WorkerError(
                        f"{self.spec.name} 进程启动失败（退出码 {self.proc.returncode}），"
                        f"详情见 {self.log_path}"
                    )
                with contextlib.suppress(httpx.HTTPError):
                    resp = await client.get(url, headers={"Authorization": f"Bearer {self.token}"})
                    if resp.status_code < 500:
                        return
                await asyncio.sleep(0.5)

    async def _drain_stdout(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            self._write_log(line)

    def _write_log(self, line: bytes) -> None:
        if self._log_file is not None and not self._log_file.closed:
            self._log_file.write(line)
            self._log_file.flush()

    async def stop(self, grace: float = 8.0) -> None:
        if not self.alive:
            await self._cleanup()
            return
        if self.kit is not None:
            await self.kit.shutdown()
        else:
            assert self.proc is not None
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
        try:
            assert self.proc is not None
            await asyncio.wait_for(self.proc.wait(), timeout=grace)
        except TimeoutError:
            await self._kill()
        await self._cleanup()

    async def _kill(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            with contextlib.suppress(Exception):
                await self.proc.wait()
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self.kit is not None:
            await self.kit.aclose()
            self.kit = None
        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._drain_task
            self._drain_task = None
        if self._log_file is not None and not self._log_file.closed:
            self._log_file.close()
        self.port = None


ExitCallback = Callable[[str], Awaitable[None] | None]


class WorkerManager:
    def __init__(
        self,
        specs: Sequence[WorkerSpec],
        *,
        logs_dir: Path,
        token: str,
        env: dict[str, str] | None = None,
        on_exit: ExitCallback | None = None,
    ) -> None:
        self.specs = {s.name: s for s in specs}
        self._logs_dir = logs_dir
        self._token = token
        self._env = env or {}
        self._procs: dict[str, WorkerProcess] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._stopping: set[str] = set()
        self.on_exit = on_exit

    def available(self, name: str) -> bool:
        return self.specs[name].command is not None

    def get(self, name: str) -> WorkerProcess | None:
        proc = self._procs.get(name)
        return proc if proc is not None and proc.alive else None

    async def start(self, name: str, model_source: str | None = None) -> WorkerProcess:
        existing = self.get(name)
        if existing is not None:
            return existing
        spec = self.specs[name]
        proc = WorkerProcess(
            spec,
            token=self._token,
            log_path=self._logs_dir / f"worker-{name}.log",
            env={**self._env, **spec.env},
        )
        await proc.start(model_source)
        self._procs[name] = proc
        self._watchers[name] = asyncio.create_task(self._watch(name, proc))
        return proc

    async def _watch(self, name: str, proc: WorkerProcess) -> None:
        assert proc.proc is not None
        code = await proc.proc.wait()
        if name in self._stopping:
            return
        log.warning("worker %s exited unexpectedly with code %s", name, code)
        await proc.stop()
        if self._procs.get(name) is proc:
            del self._procs[name]
        if self.on_exit is not None:
            result = self.on_exit(name)
            if asyncio.iscoroutine(result):
                await result

    async def stop(self, name: str) -> None:
        proc = self._procs.pop(name, None)
        watcher = self._watchers.pop(name, None)
        if proc is None:
            return
        self._stopping.add(name)
        try:
            await proc.stop()
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
        finally:
            self._stopping.discard(name)

    async def stop_all(self) -> None:
        for name in list(self._procs):
            await self.stop(name)

    def status(self) -> list[dict[str, Any]]:
        out = []
        for name, spec in self.specs.items():
            proc = self.get(name)
            out.append(
                {
                    "name": name,
                    "installed": spec.command is not None,
                    "unavailable_reason": spec.unavailable_reason,
                    "running": proc is not None,
                    "port": proc.port if proc else None,
                    "model_source": proc.model_source if proc else None,
                    "uptime_s": (time.monotonic() - proc.started_at)
                    if proc and proc.started_at
                    else None,
                }
            )
        return out
