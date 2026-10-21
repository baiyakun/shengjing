"""下载模型的子进程：python -m shengjing_engine.model_download < 任务.json

单独一个进程，是为了能随时取消（结束进程即可，已下载的部分下次会接着下）。
从标准输入读一个 JSON 任务，每行向标准输出写一个 JSON 事件：

    {"event": "plan", "total": 字节, "present": 已有字节}
    {"event": "progress", "done": 字节, "total": 字节}
    {"event": "done", "artifacts": [...], "bytes": 字节}
    {"event": "error", "kind": "auth|not_found|network|disk|error", "message": "..."}

只查大小时（"mode": "sizes"）每个模型输出一行 {"event": "size", "model": ..., "total": ...}。

文件放进 HF_HOME 下的 Hugging Face 缓存，和模型进程用的是同一个位置，
所以模型进程加载时会直接用上，不再重复下载。ACE-Step 的主模型用它自带的下载器，
放到 ACE-Step 的模型目录（它下载后还要同步一些代码文件，自己下会漏掉这一步）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

# 磁盘至少留出这么多余量，免得下满了影响系统
DISK_MARGIN = 2 * 1024**3

ACESTEP_SCRIPT = """
import sys
from pathlib import Path
from acestep.model_downloader import ensure_main_model
ok, msg = ensure_main_model(Path(sys.argv[1]), None, sys.argv[2] or None)
print(msg, flush=True)
sys.exit(0 if ok else 1)
"""


def emit(**event: Any) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            p = Path(root) / name
            try:
                if not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    return total


def repo_cache_dir(hf_home: Path, repo_id: str) -> Path:
    return hf_home / "hub" / ("models--" + repo_id.replace("/", "--"))


def classify(exc: BaseException) -> tuple[str, str]:
    """把下载异常归成几类，给出能看懂的说明。"""
    from huggingface_hub import errors

    name = type(exc).__name__
    if isinstance(exc, errors.GatedRepoError):
        return "auth", ("这个模型需要先在 Hugging Face 页面同意使用条款，"
                        "并在设置 → 下载与生成里填写访问令牌")
    if isinstance(exc, errors.RepositoryNotFoundError):
        return "not_found", "Hugging Face 上找不到这个模型仓库，或者没有权限访问"
    if isinstance(exc, errors.RevisionNotFoundError):
        return "not_found", "模型仓库里找不到要下载的版本"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "disk", "磁盘空间不足"
    if isinstance(exc, errors.HfHubHTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return "auth", "没有权限下载这个模型，请检查 Hugging Face 访问令牌"
        return "network", f"下载出错（HTTP {status}），可以稍后重试或在设置里换下载源"
    if isinstance(exc, (errors.LocalEntryNotFoundError, ConnectionError, TimeoutError)) or (
        "Connect" in name or "Timeout" in name
    ):
        return "network", "连不上下载源。国内网络可以在设置 → 下载与生成里换成镜像站"
    return "error", f"{name}: {exc}"


def plan_hub(art: dict[str, Any]) -> dict[str, Any]:
    """用 dry_run 算出要下载哪些文件、多大、已经有多少。"""
    from huggingface_hub import snapshot_download

    infos = snapshot_download(art["repo_id"], allow_patterns=art.get("allow_patterns"),
                              dry_run=True)
    total = sum(i.file_size for i in infos)
    present = sum(i.file_size for i in infos if not i.will_download)
    commit = infos[0].commit_hash if infos else ""
    return {"total": total, "present": present, "commit": commit,
            "files": [i.filename for i in infos]}


def plan_acestep(art: dict[str, Any], local_dir: Path) -> dict[str, Any]:
    from huggingface_hub import HfApi

    try:
        info = HfApi().model_info(art["repo_id"], files_metadata=True)
        total = sum(s.size or 0 for s in info.siblings or [])
    except Exception:  # noqa: BLE001 - 拿不到大小也能下载，只是没有百分比
        total = 0
    present = min(dir_size(local_dir), total) if total else dir_size(local_dir)
    return {"total": total, "present": present, "commit": "", "files": []}


class Watcher(threading.Thread):
    """每半秒量一次下载目录的大小，换算成进度。"""

    def __init__(self, dirs: list[Path], present: int, total: int) -> None:
        super().__init__(daemon=True)
        self.dirs = dirs
        self.base = sum(dir_size(d) for d in dirs)
        self.present = present
        self.total = total
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(0.5):
            grown = sum(dir_size(d) for d in self.dirs) - self.base
            done = self.present + max(0, grown)
            emit(event="progress", done=min(done, self.total) if self.total else done,
                 total=self.total)


def download(job: dict[str, Any]) -> int:
    hf_home = Path(os.environ["HF_HOME"])
    acestep_dir = Path(job["acestep_dir"]) if job.get("acestep_dir") else None
    arts = job["artifacts"]
    try:
        plans = [plan_acestep(a, acestep_dir) if a.get("acestep") else plan_hub(a) for a in arts]
    except Exception as exc:  # noqa: BLE001
        kind, message = classify(exc)
        emit(event="error", kind=kind, message=message)
        return 1
    total = sum(p["total"] for p in plans)
    present = sum(p["present"] for p in plans)
    emit(event="plan", total=total, present=present)

    hf_home.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(hf_home).free
    if total - present > free - DISK_MARGIN:
        gb = (total - present) / 1024**3
        emit(event="error", kind="disk",
             message=f"磁盘空间不足：还要下载 {gb:.1f} GB，剩余 {free / 1024**3:.1f} GB")
        return 1

    watch: list[Path] = []
    for a in arts:
        if a.get("acestep"):
            assert acestep_dir is not None
            acestep_dir.mkdir(parents=True, exist_ok=True)
            watch.append(acestep_dir)
        else:
            blobs = repo_cache_dir(hf_home, a["repo_id"]) / "blobs"
            blobs.mkdir(parents=True, exist_ok=True)
            watch.append(blobs)
    watcher = Watcher(watch, present, total)
    watcher.start()

    from huggingface_hub import snapshot_download

    done: list[dict[str, Any]] = []
    try:
        for a, plan in zip(arts, plans, strict=True):
            if a.get("acestep"):
                assert acestep_dir is not None
                env = {**os.environ, "ACESTEP_CHECKPOINTS_DIR": str(acestep_dir)}
                source = "huggingface" if os.environ.get("HF_ENDPOINT") else ""
                proc = subprocess.run(
                    [job["acestep_python"], "-c", ACESTEP_SCRIPT, str(acestep_dir), source],
                    env=env, capture_output=True, text=True,
                )
                if proc.returncode != 0:
                    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
                    emit(event="error", kind="network",
                         message="ACE-Step 模型下载失败：" + " ".join(tail))
                    return 1
                done.append({"repo_id": a["repo_id"], "local_dir": str(acestep_dir),
                             "commit": "", "files": [], "bytes": dir_size(acestep_dir)})
            else:
                path = Path(snapshot_download(a["repo_id"],
                                              allow_patterns=a.get("allow_patterns")))
                done.append({"repo_id": a["repo_id"], "commit": path.name,
                             "files": plan["files"], "bytes": plan["total"]})
    except Exception as exc:  # noqa: BLE001
        kind, message = classify(exc)
        emit(event="error", kind=kind, message=message)
        return 1
    finally:
        watcher.stop.set()
    emit(event="progress", done=total, total=total)
    emit(event="done", artifacts=done, bytes=sum(d["bytes"] for d in done))
    return 0


def sizes(job: dict[str, Any]) -> int:
    acestep_dir = Path(job["acestep_dir"]) if job.get("acestep_dir") else None
    for model_id, arts in job["models"].items():
        try:
            plans = [plan_acestep(a, acestep_dir or Path("/nonexistent")) if a.get("acestep")
                     else plan_hub(a) for a in arts]
        except Exception as exc:  # noqa: BLE001
            kind, message = classify(exc)
            emit(event="size_error", model=model_id, kind=kind, message=message)
            continue
        emit(event="size", model=model_id, total=sum(p["total"] for p in plans))
    return 0


def main() -> int:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    job = json.loads(sys.stdin.read())
    if job.get("mode") == "sizes":
        return sizes(job)
    return download(job)


if __name__ == "__main__":
    raise SystemExit(main())
