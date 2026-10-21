"""服务容器：把引擎的各个部件组装在一起，供接口层和工作流使用。"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field, replace
from typing import Any

from .audio.ffmpeg import FFmpeg
from .config import DataPaths, RuntimePaths
from .core import catalog, recommend
from .core.db import CloudVoiceStore, Database, ProjectStore, VoiceStore
from .core.downloads import DownloadManager
from .core.events import EventHub
from .core.memory import LocalSupport, MemoryBudget, chip_name, local_support, memory_tier
from .core.scheduler import ModelScheduler
from .core.settings import SecretStore, SettingsStore
from .core.tasks import TaskQueue, UserFacingError
from .core.workers import WorkerManager, WorkerSpec
from .orchestration.llm import ChatClient, LLMError, OpenAICompatClient
from .providers import PROVIDERS, CloudHandle, ProviderContext

log = logging.getLogger(__name__)


# 启动时就要下载模型的服务（mlx-lm、ACE-Step）允许的最长启动时间
MODEL_DOWNLOAD_TIMEOUT = 3600.0


def build_worker_specs(
    runtime: RuntimePaths, paths: DataPaths, local: LocalSupport | None = None
) -> list[WorkerSpec]:
    """四类模型进程的启动方式。不能运行本地模型的 Mac（Intel 芯片）上全部不可用。"""
    specs = _worker_specs(runtime, paths)
    local = local or local_support()
    if local.supported:
        return specs
    return [replace(s, command=None, unavailable_reason=local.reason) for s in specs]


def _worker_specs(runtime: RuntimePaths, paths: DataPaths) -> list[WorkerSpec]:
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
            # mlx-lm 启动时先下载并加载模型才开始监听，首次可能要下载十几 GB
            startup_timeout=MODEL_DOWNLOAD_TIMEOUT,
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
            startup_timeout=MODEL_DOWNLOAD_TIMEOUT,
            unavailable_reason="音乐扩展包未安装（ACE-Step）",
            env={"ACESTEP_API_HOST": "127.0.0.1",
                 "ACESTEP_CHECKPOINTS_DIR": str(paths.hf_home / "acestep")},
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
    cloud_voices: CloudVoiceStore
    downloads: DownloadManager
    local: LocalSupport = field(default_factory=local_support)
    """这台机器能不能运行本地模型（Intel 芯片的 Mac 只能用在线 API）。"""
    llm_transport: Any = None
    """测试用：替换云端 LLM 的网络请求。"""

    # ---------- 模型选择 ----------

    def machine(self) -> recommend.Machine:
        return recommend.Machine(
            total_gb=self.budget.total_gb, budget_gb=self.budget.budget_gb, chip=chip_name(),
            local_supported=self.local.supported,
        )

    def recommendations(self) -> dict[str, recommend.Recommendation]:
        """按这台机器推荐的模型。设置里选定的模型不会被替换，但会影响推荐：
        例如转写改用了在线服务，朗读模型就不用和转写模型挤在一起。"""
        settings = self.settings.get()
        in_use = {k: v for k, v in settings.model_overrides.items() if v}
        if self.llm_provider() == "cloud":
            in_use["llm"] = ""
        elif settings.llm.local_model:
            in_use["llm"] = settings.llm.local_model
        return recommend.recommend(
            self.machine(),
            settings.edition,
            worker_ready=self.workers.available,
            provider_ready=self.provider_ready,
            qa_enabled=settings.qa_enabled,
            in_use=in_use,
        )

    def default_model_id(self, kind: str) -> str:
        """某类任务实际使用的默认模型：设置里选定的优先，否则用推荐的。"""
        settings = self.settings.get()
        chosen = settings.model_overrides.get(kind)
        if chosen:
            spec = catalog.get(chosen)
            if not spec.allowed_in(settings.edition):
                raise LookupError(f"{spec.name} 的许可证不允许在商用版中使用")
            return chosen
        rec = self.recommendations().get(kind)
        if rec is None or rec.model is None:
            if rec is not None and rec.reason:
                raise LookupError(rec.reason)
            label = "商用版" if settings.edition == "commercial" else "个人版"
            raise LookupError(f"{label}没有可用的“{recommend.KIND_LABEL.get(kind, kind)}”模型")
        return rec.model

    def model_for(self, kind: str, requested: str | None = None) -> catalog.ModelSpec:
        settings = self.settings.get()
        if requested:
            spec = catalog.get(requested)
            if not spec.allowed_in(settings.edition):
                raise UserFacingError(f"{spec.name} 的许可证不允许在商用版中使用")
            return spec
        try:
            return catalog.get(self.default_model_id(kind))
        except (LookupError, KeyError) as exc:
            raise UserFacingError(str(exc).strip("'\"")) from exc

    def require_worker(self, spec: catalog.ModelSpec) -> None:
        if spec.worker == "cloud":
            if not self.provider_ready(spec.source):
                name = PROVIDERS[spec.source].name
                raise UserFacingError(f"请先在设置里填写 {name} 的 API Key")
            return
        if not self.workers.available(spec.worker):
            reason = self.workers.specs[spec.worker].unavailable_reason
            raise UserFacingError(reason or f"{spec.worker} 进程不可用")

    # ---------- 模型下载 ----------

    def hub_env(self) -> dict[str, str]:
        """下载模型用的环境变量：缓存位置、下载源（镜像）、访问令牌。"""
        env = {"HF_HOME": str(self.paths.hf_home)}
        endpoint = self.settings.get().hf_endpoint.strip()
        if endpoint:
            env["HF_ENDPOINT"] = endpoint.rstrip("/")
        token = self.secrets.get("hf_token")
        if token:
            env["HF_TOKEN"] = token
        return env

    def refresh_hub_env(self) -> None:
        """改了下载源或令牌后，之后启动的模型进程使用新的设置（不用重启 App）。"""
        self.workers.set_env(self.hub_env())

    def music_python(self) -> str | None:
        cmd = self.runtime.acestep_command
        return cmd[0] if cmd else None

    async def prepare_model(self, spec: catalog.ModelSpec) -> None:
        """加载模型前确保已下载；在任务里执行时把下载进度显示在任务上。"""
        from .core.tasks import CURRENT_TASK

        ctx = CURRENT_TASK.get()

        def note(job) -> None:
            if ctx is None:
                return
            if job.state == "queued":
                ctx.note(f"等待下载 {spec.name}")
            elif job.total_bytes:
                pct = int(job.done_bytes / job.total_bytes * 100)
                ctx.note(f"下载 {spec.name} {pct}%（{job.done_bytes / 1024**3:.1f} / "
                         f"{job.total_bytes / 1024**3:.1f} GB）")
            else:
                ctx.note(f"下载 {spec.name}")

        await self.downloads.ensure(
            spec, on_progress=note, check_cancelled=ctx.check_cancelled if ctx else None
        )

    # ---------- 在线 API ----------

    def provider_ready(self, provider_id: str) -> bool:
        return self.secrets.has(f"{provider_id}_api_key")

    def provider_context(self, provider_id: str, **overrides: Any) -> ProviderContext:
        conf = self.settings.get().providers.get(provider_id)
        return ProviderContext(
            api_key=self.secrets.get(f"{provider_id}_api_key"),
            base_url=conf.base_url if conf else "",
            options=dict(conf.options) if conf else {},
            ffmpeg=self.ffmpeg,
            voices=self.cloud_voices,
            tmp_dir=self.paths.tmp / "cloud",
            **overrides,
        )

    def make_cloud_handle(self, spec: catalog.ModelSpec) -> CloudHandle:
        cls = PROVIDERS[spec.source]
        return CloudHandle(spec, cls(self.provider_context(spec.source)))

    def provider_list(self) -> list[dict[str, Any]]:
        settings = self.settings.get()
        out = []
        for pid, cls in PROVIDERS.items():
            conf = settings.providers.get(pid)
            info = cls.describe()
            info["ready"] = self.provider_ready(pid)
            info["base_url"] = conf.base_url if conf else ""
            info["values"] = dict(conf.options) if conf else {}
            info["models"] = [m.id for m in catalog.all_models() if m.source == pid
                              and m.worker == "cloud"]
            out.append(info)
        return out

    # ---------- LLM ----------

    def llm_provider(self) -> str:
        """实际使用的 LLM：不能运行本地模型的 Mac 上总是云端接口。"""
        if not self.local.supported:
            return "cloud"
        return self.settings.get().llm.provider

    def llm_model_id(self) -> str:
        """本地 LLM 的模型 ID；不能运行本地模型时为空字符串。"""
        if not self.local.supported:
            return ""
        settings = self.settings.get()
        if settings.llm.local_model:
            return settings.llm.local_model
        return self.recommendations()["llm"].model or "qwen3-4b-4bit"

    def llm_label(self) -> str:
        if self.llm_provider() == "cloud":
            model = self.settings.get().llm.cloud_model
            return f"云端 · {model}" if model else "云端（还没设置）"
        return self.llm_model_id()

    def chat_client_for_cloud(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        thinking: bool | None = None,
    ) -> ChatClient:
        """云端 LLM 客户端；参数留空时用设置里保存的值（测试连接时会传入还没保存的值）。"""
        llm = self.settings.get().llm
        base_url = (base_url if base_url is not None else llm.cloud_base_url).strip()
        model = (model if model is not None else llm.cloud_model).strip()
        if not base_url or not model:
            raise UserFacingError("请先在设置 → LLM 里填写云端 LLM 的接口地址和模型名")
        return OpenAICompatClient(
            base_url=base_url,
            model=model,
            api_key=api_key or self.secrets.get("llm_api_key"),
            thinking=llm.cloud_thinking if thinking is None else thinking,
            transport=self.llm_transport,
        )

    async def test_llm(self, **overrides: Any) -> dict[str, Any]:
        """用一句很短的对话检查云端 LLM 设置是否可用。"""
        import time

        try:
            client = self.chat_client_for_cloud(**overrides)
            started = time.monotonic()
            reply = await client.chat(
                [{"role": "user", "content": "请只回复两个字：你好"}], temperature=0, max_tokens=16
            )
        except (UserFacingError, LLMError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "reply": reply.strip()[:100],
                "seconds": round(time.monotonic() - started, 1),
                "model": client.model}

    def refresh_runtime(self) -> None:
        """安装扩展包后重新检测运行环境，更新未运行进程的启动方式（不用重启引擎）。"""
        self.runtime = RuntimePaths.from_env(self.paths)
        for spec in build_worker_specs(self.runtime, self.paths, self.local):
            if self.workers.get(spec.name) is None:
                self.workers.specs[spec.name] = spec

    # ---------- 状态 ----------

    def model_list(self) -> list[dict[str, Any]]:
        settings = self.settings.get()
        machine = self.machine()
        recs = self.recommendations()
        defaults: dict[str, str] = {}
        for kind in {m.kind for m in catalog.all_models()}:
            if kind == "llm":
                if self.llm_provider() == "local":
                    defaults[kind] = self.llm_model_id()
                continue
            with contextlib.suppress(LookupError, KeyError):
                defaults[kind] = self.default_model_id(kind)
        out = []
        for m in catalog.all_models():
            item = m.to_dict()
            item["allowed"] = m.allowed_in(settings.edition)
            item["default"] = defaults.get(m.kind) == m.id
            item["recommended"] = recs.get(m.kind) is not None and recs[m.kind].model == m.id
            item["fits"] = recommend.fits(m, machine)
            item["worker_installed"] = (
                self.provider_ready(m.source) if m.worker == "cloud"
                else self.workers.available(m.worker)
            )
            item["download"] = None if m.worker == "cloud" else self.downloads.status(m.id)
            # 这台 Mac 能不能用（Intel 芯片的 Mac 上只有在线服务能用）
            item["runs_here"] = m.worker == "cloud" or self.local.supported
            out.append(item)
        return out

    def system_info(self) -> dict[str, Any]:
        settings = self.settings.get()
        machine = self.machine()
        return {
            "chip": machine.chip,
            "chip_class": machine.chip_class,
            "memory_total_gb": round(self.budget.total_gb, 1),
            "memory_budget_gb": round(self.budget.budget_gb, 1),
            "memory_tier": memory_tier(self.budget.total_gb),
            "edition": settings.edition,
            "ffmpeg": self.ffmpeg.available,
            "llm_default": self.llm_label(),
            "llm_provider": self.llm_provider(),
            "local": self.local.to_dict(),
            "recommendations": {k: r.to_dict() for k, r in self.recommendations().items()},
            "scheduler": self.scheduler.status(),
        }


def create_services(
    paths: DataPaths, runtime: RuntimePaths, token: str, *, local: LocalSupport | None = None
) -> EngineServices:
    """组装引擎。不启动任何后台循环（由接口层的 lifespan 负责）。"""
    paths.ensure()
    db = Database(paths.db)
    settings = SettingsStore(paths.settings)
    secrets = SecretStore()
    events = EventHub()
    tasks = TaskQueue(db, events, paths.outputs)
    budget = MemoryBudget.detect(settings.get().memory_reserve_gb)

    local = local or local_support()
    workers = WorkerManager(
        build_worker_specs(runtime, paths, local), logs_dir=paths.logs, token=token
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
        cloud_voices=CloudVoiceStore(db),
        downloads=None,  # type: ignore[arg-type]  # 下面创建，它要用到 services 的方法
        local=local,
    )
    services.downloads = DownloadManager(
        paths.hf_home,
        hub_env=services.hub_env,
        music_python=services.music_python,
        publish=events.publish,
        local=services.local,
    )
    services.refresh_hub_env()
    scheduler.cloud_factory = services.make_cloud_handle
    scheduler.prepare = services.prepare_model
    tasks.bind(services)
    from .workflows import register_all

    register_all(tasks)
    return services
