"""内存规划：决定加载一个模型前要卸载哪些模型、关闭哪些进程。

这一层是纯逻辑，不碰真实进程，方便测试。真正执行动作的是 scheduler.py。

规则：
1. 模型已加载：什么都不做。
2. 内存不够时，按“最久没用”的顺序腾出空间；正在使用（pinned）的模型不能动。
3. 如果被腾出的模型是某个进程里的最后一个模型，就直接关闭整个进程，
   因为结束进程是唯一能保证内存全部还给系统的办法。
4. 单模型进程（LLM、音乐）换模型时必须重启进程。
5. 腾完还不够，就报错，并给出需要多少、可用多少。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActionKind = Literal["start_worker", "stop_worker", "load", "unload"]

# 每个进程本身（Python 解释器 + 框架）的大致开销
WORKER_OVERHEAD_GB = 0.6


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    worker: str
    model: str | None = None


class InsufficientMemory(RuntimeError):
    def __init__(self, model: str, need_gb: float, available_gb: float) -> None:
        self.model = model
        self.need_gb = need_gb
        self.available_gb = available_gb
        super().__init__(
            f"内存不足：加载 {model} 需要约 {need_gb:.1f} GB，"
            f"目前最多能腾出 {available_gb:.1f} GB。"
            "可以换一个更小的模型，或关闭其他占内存的应用。"
        )


@dataclass
class WorkerState:
    name: str
    single_model: bool = False
    running: bool = False
    loaded: dict[str, float] = field(default_factory=dict)
    """已加载的模型 → 估算内存（GB）。"""


@dataclass
class MemoryPlanner:
    budget_gb: float
    workers: dict[str, WorkerState] = field(default_factory=dict)
    last_used: dict[str, float] = field(default_factory=dict)
    pinned: dict[str, int] = field(default_factory=dict)
    _clock: float = 0.0

    def register_worker(self, name: str, *, single_model: bool = False) -> None:
        self.workers.setdefault(name, WorkerState(name=name, single_model=single_model))

    # ---------- 查询 ----------

    def used_gb(self) -> float:
        total = 0.0
        for w in self.workers.values():
            if w.running:
                total += WORKER_OVERHEAD_GB + sum(w.loaded.values())
        return total

    def free_gb(self) -> float:
        return self.budget_gb - self.used_gb()

    def is_loaded(self, worker: str, model: str) -> bool:
        w = self.workers[worker]
        return w.running and model in w.loaded

    # ---------- 规划 ----------

    def plan(self, worker: str, model: str, memory_gb: float) -> list[Action]:
        """返回要执行的动作列表。不修改状态，执行成功后调用 apply()。"""
        if worker not in self.workers:
            raise KeyError(f"未注册的进程：{worker}")
        target = self.workers[worker]
        if target.running and model in target.loaded:
            return []

        actions: list[Action] = []
        # 用一份副本模拟执行过程
        sim = {name: (w.running, dict(w.loaded)) for name, w in self.workers.items()}

        def sim_used() -> float:
            return sum(
                WORKER_OVERHEAD_GB + sum(loaded.values())
                for running, loaded in sim.values()
                if running
            )

        # 单模型进程换模型：先关掉旧进程
        if target.single_model and target.running and target.loaded:
            current = next(iter(target.loaded))
            if self.pinned.get(current):
                raise RuntimeError(f"{current} 正在使用中，暂时不能切换到 {model}")
            actions.append(Action("stop_worker", worker))
            sim[worker] = (False, {})

        need = memory_gb + (0.0 if sim[worker][0] else WORKER_OVERHEAD_GB)

        while self.budget_gb - sim_used() < need:
            victim = self._pick_victim(sim)
            if victim is None:
                raise InsufficientMemory(model, need, max(0.0, self.budget_gb - sim_used()))
            vworker, vmodel = victim
            running, loaded = sim[vworker]
            vstate = self.workers[vworker]
            if len(loaded) == 1 and (vworker != worker or vstate.single_model):
                actions.append(Action("stop_worker", vworker))
                sim[vworker] = (False, {})
                if vworker == worker:
                    need = memory_gb + WORKER_OVERHEAD_GB
            else:
                actions.append(Action("unload", vworker, vmodel))
                loaded = dict(loaded)
                loaded.pop(vmodel)
                sim[vworker] = (running, loaded)

        if not sim[worker][0]:
            actions.append(Action("start_worker", worker))
        actions.append(Action("load", worker, model))
        return actions

    def _pick_victim(
        self, sim: dict[str, tuple[bool, dict[str, float]]]
    ) -> tuple[str, str] | None:
        """在模拟状态里找最久没用、且没在使用中的模型。"""
        candidates: list[tuple[float, str, str]] = []
        for wname, (running, loaded) in sim.items():
            if not running:
                continue
            for mname in loaded:
                if self.pinned.get(mname):
                    continue
                candidates.append((self.last_used.get(mname, 0.0), wname, mname))
        if not candidates:
            return None
        candidates.sort()
        _, wname, mname = candidates[0]
        return wname, mname

    # ---------- 状态更新（动作执行成功后调用） ----------

    def apply(self, action: Action, memory_gb: float = 0.0) -> None:
        w = self.workers[action.worker]
        if action.kind == "start_worker":
            w.running = True
        elif action.kind == "stop_worker":
            w.running = False
            for m in w.loaded:
                self.last_used.pop(m, None)
            w.loaded.clear()
        elif action.kind == "load":
            assert action.model is not None
            w.running = True
            w.loaded[action.model] = memory_gb
            self.touch(action.model)
        elif action.kind == "unload":
            assert action.model is not None
            w.loaded.pop(action.model, None)

    def mark_stopped(self, worker: str) -> None:
        """进程意外退出时调用。"""
        self.apply(Action("stop_worker", worker))

    def touch(self, model: str) -> None:
        self._clock += 1.0
        self.last_used[model] = self._clock

    def pin(self, model: str) -> None:
        self.pinned[model] = self.pinned.get(model, 0) + 1
        self.touch(model)

    def unpin(self, model: str) -> None:
        count = self.pinned.get(model, 0) - 1
        if count <= 0:
            self.pinned.pop(model, None)
        else:
            self.pinned[model] = count
