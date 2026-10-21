"""服务容器：把引擎的各个部件组装在一起，供接口层和工作流使用。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .audio.ffmpeg import FFmpeg
from .config import DataPaths, RuntimePaths
from .core import catalog
from .core.db import Database, ProjectStore, VoiceStore
from .core.events import EventHub
from .core.memory import MemoryBudget, chip_name, memory_tier
from .core.scheduler import ModelScheduler
from .core.settings import SecretStore, SettingsStore
from .core.tasks import TaskQueue, UserFacingError
from .core.workers import WorkerManager, WorkerSpec
from .orchestration.llm import ChatClient, OpenAICompatClient

log = logging.getLogger(__name__)


def build_worker_specs(runtime: RuntimePaths, paths: DataPaths) -> list[WorkerSpec]:
    """四类模型进程的启动方式。"""
    base = runtime.base_python

    def kit(module: str):
        return lambda port, token, source: [base, "-m", module, "--port", str(port)]

    def mlx_lm(port: int, token: str, source: str | None):
        if not source:
            raise ValueError("LLM 进程启动时必须指定模型")
        return [base, "-m", "mlx_lm.server", "--model", source, "--host", "127.0.0.1",
                "--port", str(port)]

    analysis_cmd = None
    if runtime.analysis_python:
        analysis_python = runtime.analysis_python

        def analysis_cmd(port: int, token: str, source: str | None):
            return [analysis_python, "-m", "shengjing_analysis_worker", "--port", str(port)]

    music_cmd = None
    if runtime.acestep_command:
        acestep = list(runtime.acestep_command)

        def music_cmd(port: int, token: str, source: str | None):
            # ACE-Step 从环境变量读地址、端口和 API Key（见其 docs/en/API.md 第 13 节）
            return acestep

    return [
        WorkerSpec(name="speech", protocol="kit", command=kit("shengjing_speech_worker")),
        WorkerSpec(
            name="llm",
            protocol="openai",
            command=mlx_lm,
            single_model=True,
            health_path="/v1/models",
        ),
        WorkerSpec(
            name="analysis",
            protocol="kit",
            command=analysis_cmd,
            unavailable_reason="分析扩展包未安装（说话人区分、时间戳、人声分离需要它）",
        ),
        WorkerSpec(
            name="music",
            protocol="acestep",
            command=music_cmd,
            single_model=True,
            health_path="/health",
            unavailable_reason="音乐扩展包未安装（ACE-Step）",
            env={"ACESTEP_API_HOST": "127.0.0.1"},
            port_env="ACESTEP_API_PORT",
            token_env="ACESTEP_API_KEY",
        ),
    ]


@dataclass
class EngineServices:
    paths: DataPaths
    runtime: RuntimePaths
    token: str
    db: Database
    settings: SettingsStore
    secrets: SecretStore
    events: EventHub
    tasks: TaskQueue
    voices: VoiceStore
    projects: ProjectStore
    workers: WorkerManager
    scheduler: ModelScheduler
    budget: MemoryBudget
    ffmpeg: FFmpeg

    # ---------- 模型选择 ----------

    def model_for(self, kind: str, requested: str | None = None) -> catalog.ModelSpec:
        settings = self.settings.get()
        if requested:
            spec = catalog.get(requested)
            if not spec.allowed_in(settings.edition):
                raise UserFacingError(f"{spec.name} 的许可证不允许在商用版中使用")
            return spec
        try:
            return catalog.get(
                catalog.default_model(kind, settings.edition, settings.model_overrides)
            )
        except LookupError as exc:
            raise UserFacingError(str(exc)) from exc

    def require_worker(self, spec: catalog.ModelSpec) -> None:
        if not self.workers.available(spec.worker):
            reason = self.workers.specs[spec.worker].unavailable_reason
            raise UserFacingError(reason or f"{spec.worker} 进程不可用")

    # ---------- LLM ----------

    def llm_model_id(self) -> str:
        settings = self.settings.get()
        return settings.llm.local_model or catalog.default_llm_for_memory(self.budget.total_gb)

    def chat_client_for_cloud(self) -> ChatClient:
        settings = self.settings.get()
        if not settings.llm.cloud_base_url or not settings.llm.cloud_model:
            raise UserFacingError("请先在设置里填写云端 LLM 的接口地址和模型名")
        return OpenAICompatClient(
            base_url=settings.llm.cloud_base_url,
            model=settings.llm.cloud_model,
            api_key=self.secrets.get("llm_api_key"),
        )

    def refresh_runtime(self) -> None:
        """安装扩展包后重新检测运行环境，更新未运行进程的启动方式（不用重启引擎）。"""
        self.runtime = RuntimePaths.from_env(self.paths)
        for spec in build_worker_specs(self.runtime, self.paths):
            if self.workers.get(spec.name) is None:
                self.workers.specs[spec.name] = spec

    # ---------- 状态 ----------

    def model_list(self) -> list[dict[str, Any]]:
        settings = self.settings.get()
        defaults: dict[str, str] = {}
        for kind in {m.kind for m in catalog.all_models()}:
            if kind == "llm":
                defaults[kind] = self.llm_model_id()
                continue
            try:
                defaults[kind] = catalog.default_model(
                    kind, settings.edition, settings.model_overrides
                )
            except LookupError:
                pass
        out = []
        for m in catalog.all_models():
            item = m.to_dict()
            item["allowed"] = m.allowed_in(settings.edition)
            item["default"] = defaults.get(m.kind) == m.id
            item["worker_installed"] = self.workers.available(m.worker)
            out.append(item)
        return out

    def system_info(self) -> dict[str, Any]:
        settings = self.settings.get()
        return {
            "chip": chip_name(),
            "memory_total_gb": round(self.budget.total_gb, 1),
            "memory_budget_gb": round(self.budget.budget_gb, 1),
            "memory_tier": memory_tier(self.budget.total_gb),
            "edition": settings.edition,
            "ffmpeg": self.ffmpeg.available,
            "llm_default": self.llm_model_id(),
            "scheduler": self.scheduler.status(),
        }


def create_services(paths: DataPaths, runtime: RuntimePaths, token: str) -> EngineServices:
    """组装引擎。不启动任何后台循环（由接口层的 lifespan 负责）。"""
    paths.ensure()
    db = Database(paths.db)
    settings = SettingsStore(paths.settings)
    secrets = SecretStore()
    events = EventHub()
    tasks = TaskQueue(db, events, paths.outputs)
    budget = MemoryBudget.detect(settings.get().memory_reserve_gb)

    worker_env = {"HF_HOME": str(paths.hf_home)}
    endpoint = settings.get().hf_endpoint
    if endpoint:
        worker_env["HF_ENDPOINT"] = endpoint
    hf_token = secrets.get("hf_token")
    if hf_token:
        worker_env["HF_TOKEN"] = hf_token

    workers = WorkerManager(
        build_worker_specs(runtime, paths), logs_dir=paths.logs, token=token, env=worker_env
    )
    scheduler = ModelScheduler(workers, budget.budget_gb)
    services = EngineServices(
        paths=paths,
        runtime=runtime,
        token=token,
        db=db,
        settings=settings,
        secrets=secrets,
        events=events,
        tasks=tasks,
        voices=VoiceStore(db, paths.voices),
        projects=ProjectStore(db, paths.projects),
        workers=workers,
        scheduler=scheduler,
        budget=budget,
        ffmpeg=FFmpeg(runtime.ffmpeg),
    )
    tasks.bind(services)
    from .workflows import register_all

    register_all(tasks)
    return services
