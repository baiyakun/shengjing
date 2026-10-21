"""CI 用：在 Intel Mac 上启动真实的主引擎，确认它进入在线模式。

用法：python scripts/check_online_mode.py（用装了主引擎的 Python 运行）
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request


def get(port: int, path: str):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers={"Authorization": "Bearer ci"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def main() -> int:
    env = {**os.environ, "SHENGJING_ENGINE_TOKEN": "ci", "SHENGJING_SECRETS": "memory"}
    with tempfile.TemporaryDirectory() as data:
        proc = subprocess.Popen(
            [sys.executable, "-m", "shengjing_engine", "--port", "0", "--data-dir", data],
            stdout=subprocess.PIPE, text=True, env=env,
        )
        try:
            port = None
            for line in proc.stdout:
                if '"ready"' in line:
                    port = json.loads(line)["port"]
                    break
            assert port, "引擎没有启动"
            info = get(port, "/system")
            print("local:", info["local"])
            assert info["local"]["supported"] is False, "Intel Mac 上应该关闭本地模型"
            assert info["local"]["arch"] == "x86_64"
            assert all(not w["installed"] for w in info["scheduler"]["workers"])
            runnable = [m["id"] for m in get(port, "/catalog") if m["runs_here"]]
            assert runnable and all("-" in m and m.split("-")[0] in
                                    {"openai", "senseaudio", "fish", "elevenlabs"}
                                    for m in runnable), runnable
            print(f"在线模式正常：{len(runnable)} 个在线模型可用")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
