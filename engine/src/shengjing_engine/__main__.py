"""引擎入口。

由 Tauri 外壳启动：

    python -m shengjing_engine --port 0 --data-dir <目录>

令牌通过环境变量 SHENGJING_ENGINE_TOKEN 传入（不放在命令行里，避免被 ps 看到）。
启动完成后在标准输出打印一行 JSON：{"event": "ready", "port": 54321}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
import sys
from pathlib import Path

import uvicorn

from . import __version__
from .api.app import create_app
from .config import DataPaths, RuntimePaths, default_data_dir
from .services import create_services


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="shengjing-engine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 表示自动选择空闲端口")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    token = os.environ.get("SHENGJING_ENGINE_TOKEN") or secrets.token_hex(32)
    if "SHENGJING_ENGINE_TOKEN" not in os.environ:
        logging.warning("没有传入 SHENGJING_ENGINE_TOKEN，已随机生成（开发模式）：%s", token)

    paths = DataPaths(args.data_dir or default_data_dir())
    paths.ensure()
    runtime = RuntimePaths.from_env(paths)
    services = create_services(paths, runtime, token)
    app = create_app(services)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    # 先开始监听再报告就绪，这样外壳收到端口后立刻发来的请求会排队而不是被拒绝
    sock.listen(128)
    port = sock.getsockname()[1]

    config = uvicorn.Config(app, log_level=args.log_level, access_log=False)
    server = uvicorn.Server(config)

    async def serve() -> None:
        print(
            json.dumps({"event": "ready", "port": port, "version": __version__}),
            flush=True,
        )
        await server.serve(sockets=[sock])

    asyncio.run(serve())


if __name__ == "__main__":
    main()
