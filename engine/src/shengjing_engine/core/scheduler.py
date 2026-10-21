"""模型调度：按内存预算加载模型，执行 planner 给出的动作。

用法::

    async with scheduler.use("fish-s2-pro") as handle:
        result = await handle.run("tts.generate", {...})

同一时间只有一个协程在改动进程状态（asyncio.Lock）。模型在 use() 块里
处于“使用中”，不会被别的任务卸载。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from . import catalog
from .planner import Action, MemoryPlanner
from .workers import KitClient, WorkerError, WorkerManager, WorkerProcess


@dataclass
class ModelHandle:
    model_id: str
    spec: catalog.ModelSpec
    process: WorkerProcess

    @property
    def base_url(self) -> str:
        return self.process.base_url

    @property
    def token(self) -> str:
        return self.process.token

    async def run(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        kit: KitClient | None = self.process.kit
        if kit is None:
            raise WorkerError(f"{self.spec.worker} 进程不支持 kit 协议调用")
        return await kit.run(op, self.model_id, params)


class ModelScheduler:
    def __init__(
        self,
        manager: WorkerManager,
        budget_gb: float,
        *,
        source_overrides: dict[str, str] | None = None,
    ) -> None:
        self.manager = manager
        self.planner = MemoryPlanner(budget_gb=budget_gb)
        for name, spec in manager.specs.items():
            self.planner.register_worker(name, single_model=spec.single_model)
        self._lock = asyncio.Lock()
        self._last_active: dict[str, float] = {}
        self._source_overrides = source_overrides or {}
        manager.on_exit = self._on_worker_exit

    def _on_worker_exit(self, name: str) -> None:
        self.planner.mark_stopped(name)

    def source_for(self, spec: catalog.ModelSpec) -> str:
        return self._source_overrides.get(spec.id, spec.source)

    @contextlib.asynccontextmanager
    async def use(self, model_id: str) -> AsyncIterator[ModelHandle]:
        spec = catalog.get(model_id)
        async with self._lock:
            actions = self.planner.plan(spec.worker, model_id, spec.memory_gb)
            for action in actions:
                await self._execute(action, spec)
            self.planner.pin(model_id)
            process = self.manager.get(spec.worker)
            if process is None:
                self.planner.unpin(model_id)
                raise WorkerError(f"{spec.worker} 进程没有运行")
        try:
            yield ModelHandle(model_id=model_id, spec=spec, process=process)
        finally:
            self.planner.unpin(model_id)
            self._last_active[spec.worker] = time.monotonic()

    async def _execute(self, action: Action, target: catalog.ModelSpec) -> None:
        if action.kind == "stop_worker":
            await self.manager.stop(action.worker)
            self.planner.apply(action)
        elif action.kind == "start_worker":
            spec = self.manager.specs[action.worker]
            # 单模型进程（LLM、音乐）启动时就带上模型
            source = self.source_for(target) if spec.single_model else None
            await self.manager.start(action.worker, model_source=source)
            self.planner.apply(action)
        elif action.kind == "load":
            assert action.model is not None
            model_spec = catalog.get(action.model)
            process = self.manager.get(action.worker)
            if process is None:
                raise WorkerError(f"{action.worker} 进程没有运行")
            if process.kit is not None:
                await process.kit.load(
                    action.model, self.source_for(model_spec), dict(model_spec.load_options)
                )
            self.planner.apply(action, model_spec.memory_gb)
        elif action.kind == "unload":
            assert action.model is not None
            process = self.manager.get(action.worker)
            if process is not None and process.kit is not None:
                await process.kit.unload(action.model)
            self.planner.apply(action)

    async def stop_idle(self, idle_seconds: float) -> list[str]:
        """关闭空闲超过 idle_seconds 且没有模型在使用中的进程。"""
        stopped = []
        async with self._lock:
            now = time.monotonic()
            for name, state in self.planner.workers.items():
                if not state.running:
                    continue
                if any(self.planner.pinned.get(m) for m in state.loaded):
                    continue
                last = self._last_active.get(name, 0.0)
                if now - last >= idle_seconds:
                    await self.manager.stop(name)
                    self.planner.apply(Action("stop_worker", name))
                    stopped.append(name)
        return stopped

    async def stop_worker(self, name: str) -> None:
        async with self._lock:
            state = self.planner.workers[name]
            if any(self.planner.pinned.get(m) for m in state.loaded):
                raise WorkerError(f"{name} 进程里有模型正在使用，不能关闭")
            await self.manager.stop(name)
            self.planner.apply(Action("stop_worker", name))

    def status(self) -> dict[str, Any]:
        return {
            "budget_gb": self.planner.budget_gb,
            "used_gb": round(self.planner.used_gb(), 2),
            "workers": [
                {
                    **info,
                    "loaded": sorted(self.planner.workers[info["name"]].loaded),
                    "pinned": sorted(
                        m
                        for m in self.planner.workers[info["name"]].loaded
                        if self.planner.pinned.get(m)
                    ),
                }
                for info in self.manager.status()
            ],
        }
