"""测试用的假模型进程：只用标准库实现 kit 协议，验证进程管理逻辑。"""

from __future__ import annotations

import json
import os
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("SHENGJING_WORKER_TOKEN", "")
LOADED: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 安静
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"ok": False, "error": {"message": "bad token"}})
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/health":
            self._send(200, {"ok": True, "loaded": sorted(LOADED)})
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/load":
            LOADED.add(body["model"])
            self._send(200, {"ok": True})
        elif self.path == "/unload":
            LOADED.discard(body["model"])
            self._send(200, {"ok": True})
        elif self.path == "/run":
            if body["model"] not in LOADED:
                self._send(400, {"ok": False, "error": {"message": "not loaded"}})
            elif body["op"] == "crash":
                os._exit(3)
            else:
                self._send(200, {"ok": True, "result": {"echo": body["params"]}})
        elif self.path == "/shutdown":
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, {"ok": False})


class Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind 会反查本机主机名（getfqdn），在部分 Mac 上要等很久
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def main() -> None:
    server = Server(("127.0.0.1", 0), Handler)
    print("some import noise", flush=True)
    print(json.dumps({"event": "ready", "port": server.server_address[1]}), flush=True)
    server.serve_forever()
    sys.exit(0)


if __name__ == "__main__":
    main()
