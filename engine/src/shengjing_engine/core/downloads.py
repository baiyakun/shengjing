"""模型下载：每个本地模型要下载哪些文件、下没下好、下载进度、删除。

- 文件放在数据目录下的 models/（模型进程的 HF_HOME），和模型进程加载时用的是同一份缓存。
- 下载在单独的子进程里进行（model_download.py），可以随时取消，下次接着下。
- 一个模型下载完成后写一个记录文件，之后不用联网就能判断“已下载”。
- 几个模型共用的文件（例如 MOSS 系列共用的音频编解码器）删除其中一个模型时会保留。
- 任务要用的模型还没下载时，会先下载并在任务进度里显示（见 ensure()）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from . import catalog

log = logging.getLogger(__name__)

# mlx-speech 不带子目录的模型下载的文件类型（与 mlx_speech/_hub.py 保持一致）
MLX_SPEECH_PATTERNS = (
    "*.json", "*.safetensors", "*.py", "*.model", "*.tiktoken", "*.txt", "*.jsonl", "*.yaml",
    "*.jinja",
)
# mlx-lm 下载的文件类型
MLX_LM_PATTERNS = (
    "*.json", "*.safetensors", "*.py", "tokenizer.model", "*.tiktoken", "tiktoken.model",
    "*.txt", "*.jsonl", "*.jinja",
)


@dataclass(frozen=True)
class Artifact:
    repo_id: str
    allow_patterns: tuple[str, ...] | None = None
    """None 表示整个仓库。"""
    acestep: bool = False
    """ACE-Step 主模型：用 ACE-Step 自带的下载器放到它的模型目录。"""

    def to_job(self) -> dict[str, Any]:
        return {"repo_id": self.repo_id, "acestep": self.acestep,
                "allow_patterns": list(self.allow_patterns) if self.allow_patterns else None}


def _speech(repo_id: str) -> Artifact:
    return Artifact(repo_id, MLX_SPEECH_PATTERNS)


def _speech_sub(repo_id: str, subdir: str) -> Artifact:
    # mlx-speech 对共用仓库只下载对应的子目录
    return Artifact(repo_id, (f"{subdir}/**", "README.md"))


MOSS_CODEC = _speech("appautomaton/openmoss-audio-tokenizer-mlx")
DRAMABOX_GEMMA = _speech("appautomaton/gemma-3-12b-it-backbone-4bit-mlx")

# 每个本地模型要下载的内容（仓库和文件范围与 mlx-speech、mlx-lm 加载时一致）
ARTIFACTS: dict[str, tuple[Artifact, ...]] = {
    "fish-s2-pro": (_speech("appautomaton/fishaudio-s2-pro-8bit-mlx"),),
    "dots-tts-soar": (_speech_sub("appautomaton/dots-tts-mlx", "soar/mlx-int8"),),
    "dots-tts-mf": (_speech_sub("appautomaton/dots-tts-mlx", "mf/mlx-int8"),),
    "fireredtts3-base": (_speech_sub("appautomaton/fireredtts3-mlx", "base/mlx-bf16"),),
    "moss-local": (_speech("appautomaton/openmoss-tts-local-mlx"), MOSS_CODEC),
    "longcat": (_speech("appautomaton/longcat-audiodit-3.5b-8bit-mlx"),),
    "vibevoice": (_speech("appautomaton/vibevoice-mlx"),),
    "moss-ttsd": (_speech("appautomaton/openmoss-ttsd-mlx"), MOSS_CODEC),
    "dramabox": (_speech("appautomaton/dramabox-tts-3.3b-bf16-mlx"), DRAMABOX_GEMMA),
    "step-audio": (_speech("appautomaton/step-audio-editx-8bit-mlx"),),
    "moss-sound-effect": (_speech("appautomaton/openmoss-sound-effect-mlx"), MOSS_CODEC),
    "qwen3-asr-1.7b": (_speech("appautomaton/qwen3-asr-1.7b-int8-mlx"),),
    "cohere-asr": (_speech("appautomaton/cohere-asr-mlx"),),
    "granite-speech-4.0-1b": (_speech("appautomaton/granite-4.0-1b-speech-int8-mlx"),),
    "confucius4-r2t2": (_speech("appautomaton/confucius4-r2t2-bf16-mlx"),),
    "nemotron-asr-streaming": (_speech("appautomaton/nemotron-3.5-asr-streaming-0.6b-int8-mlx"),),
    "qwen3-4b-4bit": (Artifact("mlx-community/Qwen3-4B-4bit", MLX_LM_PATTERNS),),
    "qwen3.5-9b-4bit": (Artifact("mlx-community/Qwen3.5-9B-4bit", MLX_LM_PATTERNS),),
    "qwen3.6-35b-a3b-4bit": (Artifact("mlx-community/Qwen3.6-35B-A3B-4bit", MLX_LM_PATTERNS),),
    "pyannote-community-1": (Artifact("pyannote/speaker-diarization-community-1"),),
    "sortformer-streaming-v2": (Artifact("nvidia/diar_streaming_sortformer_4spk-v2"),),
    "qwen3-forced-aligner": (Artifact("Qwen/Qwen3-ForcedAligner-0.6B"),),
    "ace-step-1.5": (Artifact("ACE-Step/Ace-Step1.5", acestep=True),),
}

# 不需要（也没法）在这里下载的模型
UNMANAGED: dict[str, str] = {
    "htdemucs": "第一次分离人声时由 Demucs 自动下载（约 80 MB）",
    "silero-vad": "随分析扩展包提供，不需要下载",
}

GATED = {"pyannote-community-1"}

LocalState = Literal["ready", "partial", "missing", "unmanaged", "conversion", "unavailable"]
JobState = Literal["queued", "running", "done", "failed", "cancelled"]


def mlx_speech_mismatches() -> list[str]:
    """和 mlx-speech 自己的模型表对一遍（只能在装了 mlx-speech 的 Mac 上运行，CI 里会跑）。"""
    from mlx_speech import _hub

    # asr.list_models() 没有 detailed 参数，直接用底层的完整模型表
    table = _hub.list_models(detailed=True)
    problems = []
    for spec in catalog.all_models():
        if spec.worker != "speech" or spec.id not in ARTIFACTS:
            continue
        info = table.get(spec.source)
        if info is None:
            problems.append(f"{spec.id}: mlx-speech 没有别名 {spec.source}")
            continue
        art = ARTIFACTS[spec.id][0]
        if art.repo_id != info.repo_id:
            problems.append(f"{spec.id}: 仓库应为 {info.repo_id}，这里是 {art.repo_id}")
        expected = ((f"{info.artifact_subdir}/**", "README.md") if info.artifact_subdir
                    else tuple(getattr(_hub, "_DEFAULT_ALLOW_PATTERNS", MLX_SPEECH_PATTERNS)))
        if art.allow_patterns != expected:
            problems.append(f"{spec.id}: 文件范围应为 {expected}")
    for name, art in (("MOSS_CODEC_REPO", MOSS_CODEC), ("DRAMABOX_GEMMA_REPO", DRAMABOX_GEMMA)):
        repo = getattr(_hub, name, None)
        if repo and repo != art.repo_id:
            problems.append(f"{name}: 应为 {repo}")
    return problems


class DownloadError(Exception):
    """能直接给用户看的下载错误。"""


def repo_cache_dir(hf_home: Path, repo_id: str) -> Path:
    return hf_home / "hub" / ("models--" + repo_id.replace("/", "--"))


def acestep_dir(hf_home: Path) -> Path:
    return hf_home / "acestep"


@dataclass
class Job:
    model: str
    state: JobState = "queued"
    done_bytes: int = 0
    total_bytes: int = 0
    message: str = "排队中"
    error: str | None = None
    error_kind: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    waiters: list[asyncio.Future] = field(default_factory=list, repr=False)
    listeners: list[Callable[[Job], None]] = field(default_factory=list, repr=False)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)
                if f.name not in ("waiters", "listeners")}
        data["fraction"] = (self.done_bytes / self.total_bytes) if self.total_bytes else 0.0
        return data


class DownloadManager:
    def __init__(
        self,
        hf_home: Path,
        *,
        hub_env: Callable[[], dict[str, str]],
        music_python: Callable[[], str | None],
        publish: Callable[[dict[str, Any]], None],
        command: list[str] | None = None,
    ) -> None:
        self.hf_home = hf_home
        self.records = hf_home / ".shengjing-downloads"
        self._hub_env = hub_env
        self._music_python = music_python
        self._publish = publish
        self._command = command or [sys.executable, "-m", "shengjing_engine.model_download"]
        self.jobs: dict[str, Job] = {}
        self._pending: list[str] = []
        self._wake = asyncio.Event()
        self._proc: asyncio.subprocess.Process | None = None
        self._current: str | None = None
        self._sizes_task: asyncio.Task | None = None

    # ---------- 本地状态（不联网） ----------

    def _record_path(self, model_id: str) -> Path:
        return self.records / f"{model_id}.json"

    def record(self, model_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._record_path(model_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _record_complete(self, rec: dict[str, Any]) -> bool:
        for art in rec.get("artifacts", []):
            if art.get("local_dir"):
                if not Path(art["local_dir"]).is_dir():
                    return False
                continue
            snap = repo_cache_dir(self.hf_home, art["repo_id"]) / "snapshots" / art["commit"]
            if not all((snap / f).exists() for f in art.get("files", [])):
                return False
        return True

    def local_state(self, model_id: str) -> tuple[LocalState, int, str]:
        """(状态, 已下载字节, 说明)。"""
        spec = catalog.get(model_id)
        if spec.worker == "cloud":
            return "unmanaged", 0, "在线服务，不需要下载"
        if spec.needs_conversion:
            return "conversion", 0, "没有现成的 MLX 权重，需要先用 mlx-speech 的脚本自行转换"
        if model_id in UNMANAGED:
            return "unmanaged", 0, UNMANAGED[model_id]
        arts = ARTIFACTS.get(model_id)
        if not arts:
            return "unmanaged", 0, ""
        rec = self.record(model_id)
        if rec and self._record_complete(rec):
            return "ready", int(rec.get("bytes", 0)), ""
        if any(a.acestep for a in arts) and not self._music_python():
            return "unavailable", 0, "需要先安装音乐扩展包"
        if self._has_traces(arts[0]):
            return "partial", 0, "下载没有完成，或者是以前自动下载的；点下载会补齐并校验"
        return "missing", 0, ""

    def _has_traces(self, art: Artifact) -> bool:
        """本地有没有这个模型的文件（下了一半，或者以前由模型进程自动下载）。"""
        if art.acestep:
            folder = acestep_dir(self.hf_home)
            return folder.is_dir() and any(folder.iterdir())
        repo = repo_cache_dir(self.hf_home, art.repo_id)
        subdirs = [p[:-3] for p in art.allow_patterns or () if p.endswith("/**")]
        if subdirs:
            # 共用仓库：只看属于这个模型的子目录
            snapshots = repo / "snapshots"
            return snapshots.is_dir() and any(
                (snap / sub).exists() for snap in snapshots.iterdir() for sub in subdirs)
        return (repo / "snapshots").is_dir() or (repo / "blobs").is_dir()

    def status(self, model_id: str) -> dict[str, Any]:
        state, size, note = self.local_state(model_id)
        job = self.jobs.get(model_id)
        return {
            "state": state,
            "bytes": size,
            "note": note,
            "remote_bytes": self.sizes().get(model_id),
            "gated": model_id in GATED,
            "job": job.to_dict() if job else None,
        }

    def overview(self) -> dict[str, Any]:
        models = {m.id: self.status(m.id) for m in catalog.all_models() if m.worker != "cloud"}
        used = sum(s["bytes"] for s in models.values())
        try:
            free = shutil.disk_usage(self.hf_home).free
        except OSError:
            free = 0
        return {
            "models": models,
            "used_bytes": used,
            "free_bytes": free,
            "path": str(self.hf_home),
            "endpoint": self._hub_env().get("HF_ENDPOINT", ""),
            "sizes_updated_at": self._sizes_meta().get("updated_at"),
        }

    # ---------- 远端大小（缓存一天） ----------

    def _sizes_file(self) -> Path:
        return self.records / "sizes.json"

    def _sizes_meta(self) -> dict[str, Any]:
        try:
            return json.loads(self._sizes_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def sizes(self) -> dict[str, int]:
        return self._sizes_meta().get("sizes", {})

    def refresh_sizes(self, *, max_age: float = 86400) -> bool:
        """在后台查一遍各模型的下载大小。返回是否真的开始查询。"""
        meta = self._sizes_meta()
        if time.time() - meta.get("updated_at", 0) < max_age:
            return False
        if self._sizes_task and not self._sizes_task.done():
            return False
        self._sizes_task = asyncio.create_task(self._fetch_sizes())
        return True

    async def _fetch_sizes(self) -> None:
        models = {mid: [a.to_job() for a in arts] for mid, arts in ARTIFACTS.items()}
        job = {"mode": "sizes", "models": models, "acestep_dir": str(acestep_dir(self.hf_home))}
        found: dict[str, int] = dict(self.sizes())
        try:
            async for event in self._run_helper(job):
                if event.get("event") == "size":
                    found[event["model"]] = int(event["total"])
        except Exception:  # noqa: BLE001 - 查不到大小不影响下载
            log.exception("fetching model sizes failed")
            return
        self.records.mkdir(parents=True, exist_ok=True)
        self._sizes_file().write_text(
            json.dumps({"updated_at": time.time(), "sizes": found}), encoding="utf-8")
        self._publish({"type": "download_sizes"})

    # ---------- 下载队列 ----------

    def start(self, model_id: str) -> Job:
        state, _, note = self.local_state(model_id)
        if state in ("unmanaged", "conversion", "unavailable"):
            raise DownloadError(note or "这个模型不需要下载")
        job = self.jobs.get(model_id)
        if job and job.active:
            return job
        if state == "ready":
            raise DownloadError("这个模型已经下载好了")
        job = Job(model=model_id)
        self.jobs[model_id] = job
        self._pending.append(model_id)
        self._wake.set()
        self._emit(job)
        return job

    def prioritize(self, model_id: str) -> None:
        """任务正在等的模型排到最前面，不用等别的下载。"""
        if model_id in self._pending:
            self._pending.remove(model_id)
            self._pending.insert(0, model_id)

    def cancel(self, model_id: str) -> Job:
        job = self.jobs.get(model_id)
        if job is None or not job.active:
            raise DownloadError("这个模型没有在下载")
        if self._current == model_id and self._proc is not None:
            self._kill(self._proc)
        if model_id in self._pending:
            self._pending.remove(model_id)
        self._finish(job, "cancelled", message="已取消")
        return job

    async def run_forever(self) -> None:
        while True:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            model_id = self._pending.pop(0)
            job = self.jobs.get(model_id)
            if job is None or job.state != "queued":
                continue
            try:
                await self._run(job)
            except Exception as exc:  # noqa: BLE001 - 一个下载出错不能让整个队列停下
                log.exception("download %s crashed", model_id)
                self._finish(job, "failed", message="失败", error=str(exc), kind="error")

    async def shutdown(self) -> None:
        if self._proc is not None:
            self._kill(self._proc)

    def _emit(self, job: Job) -> None:
        self._publish({"type": "download", "download": job.to_dict()})
        for fn in list(job.listeners):
            with contextlib.suppress(Exception):
                fn(job)

    def _finish(self, job: Job, state: JobState, *, message: str = "",
                error: str | None = None, kind: str | None = None) -> None:
        if not job.active:
            return
        job.state = state
        job.message = message
        job.error = error
        job.error_kind = kind
        job.finished_at = time.time()
        self._emit(job)
        for fut in job.waiters:
            if not fut.done():
                fut.set_result(job)
        job.waiters.clear()

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        # 下载进程可能又启动了 ACE-Step 的下载器，连同子进程一起结束
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)

    async def _run_helper(self, job: dict[str, Any]):
        env = {**os.environ, **self._hub_env(), "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        self._proc = proc
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(json.dumps(job).encode())
            await proc.stdin.drain()
            proc.stdin.close()
            async for line in proc.stdout:
                with contextlib.suppress(ValueError):
                    yield json.loads(line)
            await proc.wait()
        finally:
            if proc.returncode is None:
                self._kill(proc)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 5)
            self._proc = None

    async def _run(self, job: Job) -> None:
        arts = ARTIFACTS[job.model]
        music = self._music_python()
        payload = {
            "artifacts": [a.to_job() for a in arts],
            "acestep_dir": str(acestep_dir(self.hf_home)),
            "acestep_python": music,
        }
        job.state = "running"
        job.message = "准备下载"
        job.started_at = time.time()
        self._current = job.model
        self._emit(job)
        result: dict[str, Any] | None = None
        failure: tuple[str, str] | None = None
        last_emit = 0.0
        try:
            async for event in self._run_helper(payload):
                kind = event.get("event")
                if kind == "plan":
                    job.total_bytes = int(event["total"])
                    job.done_bytes = int(event["present"])
                    job.message = "下载中"
                    self._emit(job)
                elif kind == "progress":
                    job.done_bytes = int(event["done"])
                    job.total_bytes = int(event.get("total") or job.total_bytes)
                    if time.monotonic() - last_emit >= 0.5:
                        last_emit = time.monotonic()
                        self._emit(job)
                elif kind == "done":
                    result = event
                elif kind == "error":
                    failure = (event.get("kind", "error"), event.get("message", "下载失败"))
        except Exception as exc:  # noqa: BLE001
            log.exception("download %s failed", job.model)
            failure = ("error", f"下载进程出错：{exc}")
        finally:
            self._current = None
        if job.state == "cancelled":
            return
        if result is not None:
            self.records.mkdir(parents=True, exist_ok=True)
            record = {"model": job.model, "artifacts": result["artifacts"],
                      "bytes": result["bytes"], "finished_at": time.time()}
            self._record_path(job.model).write_text(json.dumps(record), encoding="utf-8")
            # 同一批文件也可能让别的模型变成“已下载”（例如共用的仓库），不用特意处理：
            # 它们没有记录文件，会显示“下载没有完成”，点下载很快就能补上记录
            job.done_bytes = job.total_bytes = int(result["bytes"]) or job.total_bytes
            self._finish(job, "done", message="已下载")
        else:
            kind, message = failure or ("error", "下载进程意外退出")
            self._finish(job, "failed", message="失败", error=message, kind=kind)

    # ---------- 任务用到时自动下载 ----------

    async def ensure(
        self,
        spec: catalog.ModelSpec,
        *,
        on_progress: Callable[[Job], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        """模型还没下载就先下载，等下载完成。下载失败但本地已有旧文件时放行，交给模型进程处理。"""
        if spec.worker == "cloud" or spec.id not in ARTIFACTS:
            return
        state, _, _ = self.local_state(spec.id)
        if state in ("ready", "unmanaged", "conversion", "unavailable"):
            return
        job = self.jobs.get(spec.id)
        if job is None or not job.active:
            job = self.start(spec.id)
        self.prioritize(spec.id)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        job.waiters.append(fut)
        if on_progress:
            job.listeners.append(on_progress)
            on_progress(job)
        try:
            while not fut.done():
                if check_cancelled:
                    check_cancelled()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(fut), 1.0)
        finally:
            if on_progress and on_progress in job.listeners:
                job.listeners.remove(on_progress)
            if fut in job.waiters:
                job.waiters.remove(fut)
        if job.state == "done":
            return
        if state == "partial":
            log.warning("download of %s failed (%s); trying cached files", spec.id, job.error)
            return
        if job.state == "cancelled":
            raise DownloadError(f"{spec.name} 的下载已取消")
        raise DownloadError(f"{spec.name} 下载失败：{job.error}")

    # ---------- 删除 ----------

    def delete(self, model_id: str) -> None:
        job = self.jobs.get(model_id)
        if job and job.active:
            raise DownloadError("正在下载，先取消下载再删除")
        arts = ARTIFACTS.get(model_id)
        if not arts:
            raise DownloadError("这个模型没有可删除的下载文件")
        rec = self.record(model_id) or {}
        rec_files = {a["repo_id"]: set(a.get("files", [])) for a in rec.get("artifacts", [])}
        for art in arts:
            if art.acestep:
                shutil.rmtree(acestep_dir(self.hf_home), ignore_errors=True)
                continue
            self._delete_repo_files(art.repo_id, rec_files.get(art.repo_id),
                                    self._protected(model_id, art.repo_id))
        self._record_path(model_id).unlink(missing_ok=True)
        self.jobs.pop(model_id, None)
        self._publish({"type": "download_deleted", "model": model_id})

    def _protected(self, model_id: str, repo_id: str) -> set[str] | None:
        """别的已下载模型还要用的同一仓库里的文件。None 表示整个仓库都要保留。"""
        keep: set[str] = set()
        for other, arts in ARTIFACTS.items():
            if other == model_id:
                continue
            for art in arts:
                if art.repo_id != repo_id:
                    continue
                rec = self.record(other)
                if rec and self._record_complete(rec):
                    for a in rec["artifacts"]:
                        if a["repo_id"] == repo_id:
                            keep.update(a.get("files", []))
        return keep

    def _delete_repo_files(self, repo_id: str, files: set[str] | None,
                           protected: set[str] | None) -> None:
        repo = repo_cache_dir(self.hf_home, repo_id)
        if not repo.exists():
            return
        protected = protected or set()
        if not protected:
            shutil.rmtree(repo, ignore_errors=True)
            return
        snapshots = repo / "snapshots"
        keep_blobs: set[Path] = set()
        for snap in snapshots.iterdir() if snapshots.is_dir() else []:
            for rel in protected:
                p = snap / rel
                if p.exists():
                    keep_blobs.add(p.resolve())
        for snap in snapshots.iterdir() if snapshots.is_dir() else []:
            for p in sorted(snap.rglob("*"), reverse=True):
                if p.is_dir() and not p.is_symlink():
                    with contextlib.suppress(OSError):
                        p.rmdir()  # 只删空目录
                    continue
                rel = p.relative_to(snap).as_posix()
                if rel in protected or (files is not None and rel not in files):
                    continue
                blob = p.resolve()
                p.unlink(missing_ok=True)
                if blob != p and blob not in keep_blobs:
                    blob.unlink(missing_ok=True)
        blobs = repo / "blobs"
        for p in blobs.glob("*.incomplete") if blobs.is_dir() else []:
            p.unlink(missing_ok=True)
