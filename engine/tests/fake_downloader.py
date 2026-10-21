"""测试用的下载进程：不联网，按 Hugging Face 缓存的目录结构造出文件。

FAKE_DL_MODE：ok（默认）、slow（一直不结束，用来测取消）、auth（权限错误）。
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def emit(**event):
    print(json.dumps(event), flush=True)


def files_for(art):
    patterns = art.get("allow_patterns") or []
    subs = [p[:-3] for p in patterns if p.endswith("/**")]
    if subs:
        return [f"{subs[0]}/config.json", f"{subs[0]}/model.safetensors"]
    return ["config.json", "model.safetensors"]


def main():
    job = json.loads(sys.stdin.read())
    mode = os.environ.get("FAKE_DL_MODE", "ok")
    hf_home = Path(os.environ["HF_HOME"])
    if job.get("mode") == "sizes":
        for model in job["models"]:
            emit(event="size", model=model, total=2048)
        return 0
    if mode == "auth":
        emit(event="error", kind="auth", message="需要先同意使用条款")
        return 1
    arts = job["artifacts"]
    total = 1024 * len(arts)
    emit(event="plan", total=total, present=0)
    if mode == "slow":
        while True:
            emit(event="progress", done=10, total=total)
            time.sleep(0.2)
    done = []
    for art in arts:
        repo = hf_home / "hub" / ("models--" + art["repo_id"].replace("/", "--"))
        commit = "abc123"
        snap = repo / "snapshots" / commit
        names = files_for(art)
        for name in names:
            data = (art["repo_id"] + name).encode() * 32
            blob = repo / "blobs" / hashlib.sha256(data).hexdigest()
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(data)
            link = snap / name
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.exists():
                link.symlink_to(os.path.relpath(blob, link.parent))
        (repo / "refs").mkdir(exist_ok=True)
        (repo / "refs" / "main").write_text(commit)
        done.append({"repo_id": art["repo_id"], "commit": commit, "files": names, "bytes": 512})
    emit(event="progress", done=total, total=total)
    emit(event="done", artifacts=done, bytes=512 * len(arts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
