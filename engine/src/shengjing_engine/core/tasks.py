"""任务队列：SQLite 持久化，按顺序执行。

所有生成任务排成一条队，一次只跑一个。这样内存占用可预测，
也避免两个大模型同时抢内存。引擎重启后，上次没跑完的任务会重新排队。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..audio.ffmpeg import FFmpegError
from ..orchestration.llm import LLMError
from .db import Database, new_id
from .events import EventHub
from .extensions import ExtensionError
from .planner import InsufficientMemory
from .workers import WorkerError

if TYPE_CHECKING:
    from ..services import EngineServices

log = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "cancelled"}


class TaskCancelled(Exception):
    pass


class UserFacingError(Exception):
    """可以直接展示给用户的错误（不带堆栈）。"""


# 这些错误的文字本身就是写给用户看的，展示时不带异常类名
_READABLE_ERRORS = (
    UserFacingError,
    WorkerError,
    InsufficientMemory,
    LLMError,
    FFmpegError,
    ExtensionError,
)


@dataclass
class TaskContext:
    task_id: str
    kind: str
    params: dict[str, Any]
    services: EngineServices
    out_dir: Path
    _queue: TaskQueue

    def progress(self, fraction: float, message: str = "") -> None:
        self._queue._set_progress(self.task_id, max(0.0, min(1.0, fraction)), message)

    def check_cancelled(self) -> None:
        if self.task_id in self._queue._cancel_requested:
            raise TaskCancelled()


Workflow = Callable[[TaskContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class WorkflowInfo:
    kind: str
    title: str
    fn: Workflow


class TaskQueue:
    def __init__(self, db: Database, events: EventHub, outputs_dir: Path) -> None:
        self._db = db
        self._events = events
        self._outputs = outputs_dir
        self._workflows: dict[str, WorkflowInfo] = {}
        self._wakeup = asyncio.Event()
        self._cancel_requested: set[str] = set()
        self._services: EngineServices | None = None
        self._running: str | None = None

    def bind(self, services: EngineServices) -> None:
        self._services = services

    def register(self, kind: str, title: str, fn: Workflow) -> None:
        self._workflows[kind] = WorkflowInfo(kind=kind, title=title, fn=fn)

    def workflows(self) -> list[dict[str, str]]:
        return [{"kind": w.kind, "title": w.title} for w in self._workflows.values()]

    # ---------- 提交与查询 ----------

    def submit(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if kind not in self._workflows:
            raise KeyError(f"未知任务类型：{kind}")
        tid = new_id("task")
        now = time.time()
        self._db.execute(
            "INSERT INTO tasks (id, kind, status, params, created_at, updated_at) "
            "VALUES (?, ?, 'queued', ?, ?, ?)",
            (tid, kind, json.dumps(params, ensure_ascii=False), now, now),
        )
        task = self.get(tid)
        assert task is not None
        self._events.publish({"type": "task", "task": task})
        self._wakeup.set()
        return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        rows = self._db.query("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._row(rows[0]) if rows else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["status"] == "queued":
            self._update(task_id, status="cancelled", message="已取消")
        elif task["status"] == "running":
            self._cancel_requested.add(task_id)
            self._update(task_id, message="正在取消…")
        updated = self.get(task_id)
        assert updated is not None
        return updated

    def recover(self) -> int:
        """引擎启动时调用：把上次中断的任务重新排队。"""
        cur = self._db.execute(
            "UPDATE tasks SET status = 'queued', message = '引擎重启后重新排队', "
            "updated_at = ? WHERE status = 'running'",
            (time.time(),),
        )
        return cur.rowcount

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["params"] = json.loads(row["params"]) if row["params"] else {}
        row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def _update(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
        task = self.get(task_id)
        if task is not None:
            self._events.publish({"type": "task", "task": task})

    def _set_progress(self, task_id: str, fraction: float, message: str) -> None:
        self._update(task_id, progress=fraction, message=message)

    # ---------- 执行 ----------

    def _next_queued(self) -> dict[str, Any] | None:
        rows = self._db.query(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        )
        return self._row(rows[0]) if rows else None

    async def run_forever(self) -> None:
        while True:
            task = self._next_queued()
            if task is None:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            await self.run_one(task)

    async def run_one(self, task: dict[str, Any]) -> None:
        assert self._services is not None, "TaskQueue.bind() 没有调用"
        tid = task["id"]
        info = self._workflows.get(task["kind"])
        if info is None:
            self._update(tid, status="failed", error=f"未知任务类型：{task['kind']}")
            return
        out_dir = self._outputs / tid
        out_dir.mkdir(parents=True, exist_ok=True)
        ctx = TaskContext(
            task_id=tid,
            kind=task["kind"],
            params=task["params"],
            services=self._services,
            out_dir=out_dir,
            _queue=self,
        )
        self._running = tid
        self._update(tid, status="running", progress=0.0, message="开始", error=None)
        try:
            result = await info.fn(ctx)
        except TaskCancelled:
            self._update(tid, status="cancelled", message="已取消")
        except _READABLE_ERRORS as exc:
            log.warning("task %s failed: %s", tid, exc)
            self._update(tid, status="failed", error=str(exc), message="失败")
        except Exception as exc:  # noqa: BLE001 - 任务失败不能拖垮队列
            log.exception("task %s failed", tid)
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self._update(tid, status="failed", error=detail, message="失败")
        else:
            self._update(tid, status="succeeded", progress=1.0, message="完成", result=result)
        finally:
            self._cancel_requested.discard(tid)
            self._running = None
