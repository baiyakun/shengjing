"""下载子进程的逻辑：用假的 huggingface_hub 函数，不联网。"""

import io
import json
from pathlib import Path

import httpx
import huggingface_hub
from huggingface_hub import errors
from huggingface_hub.file_download import DryRunFileInfo

from shengjing_engine import model_download as md


def run(monkeypatch, job: dict, hf_home: Path) -> tuple[int, list[dict]]:
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    code = md.main()
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def fake_snapshot(hf_home: Path, sizes: dict[str, int], cached: set[str] = frozenset()):
    calls = []

    def snapshot_download(repo_id, *, allow_patterns=None, dry_run=False, **kw):
        calls.append((repo_id, allow_patterns, dry_run))
        repo = md.repo_cache_dir(hf_home, repo_id)
        if dry_run:
            return [DryRunFileInfo(commit_hash="c0ffee", file_size=size, filename=name,
                                   local_path=str(repo / name), is_cached=name in cached,
                                   will_download=name not in cached)
                    for name, size in sizes.items()]
        snap = repo / "snapshots" / "c0ffee"
        for name, size in sizes.items():
            (repo / "blobs").mkdir(parents=True, exist_ok=True)
            (repo / "blobs" / name).write_bytes(b"x" * size)
            snap.mkdir(parents=True, exist_ok=True)
            (snap / name).write_bytes(b"")
        return str(snap)

    return snapshot_download, calls


def test_download_plans_downloads_and_reports(monkeypatch, tmp_path):
    fake, calls = fake_snapshot(tmp_path, {"config.json": 10, "model.safetensors": 90},
                                cached={"config.json"})
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    code, events = run(monkeypatch, {"artifacts": [
        {"repo_id": "org/model", "allow_patterns": ["*.json", "*.safetensors"]}]}, tmp_path)
    assert code == 0, events
    assert events[0] == {"event": "plan", "total": 100, "present": 10}
    done = events[-1]
    assert done["event"] == "done" and done["bytes"] == 100
    assert done["artifacts"][0] == {"repo_id": "org/model", "commit": "c0ffee",
                                    "files": ["config.json", "model.safetensors"], "bytes": 100}
    assert calls[0] == ("org/model", ["*.json", "*.safetensors"], True)
    assert calls[1] == ("org/model", ["*.json", "*.safetensors"], False)


def test_download_refuses_when_disk_is_too_small(monkeypatch, tmp_path):
    fake, calls = fake_snapshot(tmp_path, {"model.safetensors": 10**15})
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    code, events = run(monkeypatch, {"artifacts": [{"repo_id": "org/huge"}]}, tmp_path)
    assert code == 1
    assert events[-1]["kind"] == "disk" and "磁盘空间不足" in events[-1]["message"]
    assert all(dry for _, _, dry in calls)  # 没有真正开始下载


def test_gated_repo_gives_a_readable_error(monkeypatch, tmp_path):
    def gated(repo_id, **kw):
        response = httpx.Response(403, request=httpx.Request("GET", "https://hf.co"))
        raise errors.GatedRepoError("gated", response=response)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", gated)
    code, events = run(monkeypatch, {"artifacts": [{"repo_id": "pyannote/x"}]}, tmp_path)
    assert code == 1
    assert events[-1]["kind"] == "auth" and "访问令牌" in events[-1]["message"]


def test_network_errors_suggest_a_mirror():
    kind, message = md.classify(ConnectionError("no route"))
    assert kind == "network" and "镜像" in message
    kind, _ = md.classify(OSError(28, "No space left on device"))
    assert kind == "disk"


def test_sizes_mode(monkeypatch, tmp_path):
    fake, _ = fake_snapshot(tmp_path, {"a.safetensors": 5, "b.json": 1})
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    code, events = run(monkeypatch, {"mode": "sizes", "models": {
        "m1": [{"repo_id": "org/a"}], "m2": [{"repo_id": "org/a"}, {"repo_id": "org/b"}]}},
        tmp_path)
    assert code == 0
    assert {"event": "size", "model": "m1", "total": 6} in events
    assert {"event": "size", "model": "m2", "total": 12} in events
