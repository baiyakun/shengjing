"""模型进程公共协议（kit 协议）。

主引擎通过本机 HTTP 调用模型进程，所有请求带 Authorization: Bearer <令牌>。

    GET  /health    → {"ok": true, "worker", "loaded": [...], "busy", "memory": {...}}
    POST /load      {"model", "source", "options"}           → {"ok": true, "seconds": 3.2}
    POST /unload    {"model"}                                 → {"ok": true}
    POST /run       {"op", "model", "params"}                 → {"ok": true, "result": {...}}
    POST /shutdown                                            → {"ok": true}

出错时返回 {"ok": false, "error": {"type": "...", "message": "..."}}。

启动握手：进程开始监听后，在标准输出打印一行
{"event": "ready", "port": 54321}，主引擎读到这一行就知道端口。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from typing import Any, Protocol

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

__all__ = ["Backend", "BackendError", "create_app", "serve"]

log = logging.getLogger("shengjing.worker")


class BackendError(Exception):
    """可以直接展示给用户的错误。"""


class Backend(Protocol):
    name: str

    def load(self, model: str, source: str, options: dict[str, Any]) -> None: ...

    def unload(self, model: str) -> None: ...

    def loaded(self) -> list[str]: ...

    def run(self, op: str, model: str, params: dict[str, Any]) -> dict[str, Any]: ...

    def memory(self) -> dict[str, Any]: ...


def _error(status: int, exc: BaseException) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
    )


def create_app(backend: Backend, token: str) -> FastAPI:
    app = FastAPI(title=f"shengjing-{backend.name}-worker")
    lock = threading.Lock()
    state: dict[str, Any] = {"busy": None, "server": None}

    @app.middleware("http")
    async def check_token(request: Request, call_next):
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied, token):
            return JSONResponse(status_code=401, content={"ok": False, "error": {
                "type": "Unauthorized", "message": "令牌无效"}})
        return await call_next(request)

    def guarded(label: str, fn, *args):
        with lock:
            state["busy"] = label
            try:
                return fn(*args)
            finally:
                state["busy"] = None

    @app.get("/health")
    def health() -> dict[str, Any]:
        try:
            memory = backend.memory()
        except Exception:  # noqa: BLE001 - 健康检查不能失败
            memory = {}
        return {
            "ok": True,
            "worker": backend.name,
            "loaded": backend.loaded(),
            "busy": state["busy"],
            "memory": memory,
        }

    @app.post("/load")
    def load(body: dict[str, Any]):
        started = time.monotonic()
        try:
            guarded(f"load:{body['model']}", backend.load, body["model"], body.get("source")
                    or body["model"], body.get("options") or {})
        except BackendError as exc:
            return _error(400, exc)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _error(500, exc)
        return {"ok": True, "seconds": round(time.monotonic() - started, 2)}

    @app.post("/unload")
    def unload(body: dict[str, Any]):
        try:
            guarded(f"unload:{body['model']}", backend.unload, body["model"])
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _error(500, exc)
        return {"ok": True}

    @app.post("/run")
    def run(body: dict[str, Any]):
        try:
            result = guarded(
                f"{body['op']}:{body['model']}",
                backend.run,
                body["op"],
                body["model"],
                dict(body.get("params") or {}),
            )
        except BackendError as exc:
            return _error(400, exc)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _error(500, exc)
        return {"ok": True, "result": result}

    @app.post("/shutdown")
    def shutdown():
        server = state.get("server")
        if server is not None:
            server.should_exit = True
        return {"ok": True}

    app.state.kit_state = state
    return app


def serve(
    backend: Backend,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> None:
    """启动模型进程。令牌从环境变量 SHENGJING_WORKER_TOKEN 读取。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    token = token or os.environ.get("SHENGJING_WORKER_TOKEN")
    if not token:
        raise SystemExit("缺少 SHENGJING_WORKER_TOKEN")
    app = create_app(backend, token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(64)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    app.state.kit_state["server"] = server

    async def main() -> None:
        print(json.dumps({"event": "ready", "port": sock.getsockname()[1]}), flush=True)
        await server.serve(sockets=[sock])

    asyncio.run(main())
