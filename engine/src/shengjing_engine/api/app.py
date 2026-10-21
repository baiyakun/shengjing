"""给界面调用的本机接口。

只监听 127.0.0.1，并且每个请求都要带启动时生成的令牌：
- 普通请求：Authorization: Bearer <token>
- <audio> 标签和 WebSocket 无法带请求头，改用 ?token=<token>
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from .. import __version__
from ..core import catalog
from ..core.db import new_id
from ..core.downloads import DownloadError
from ..core.extensions import EXTENSIONS
from ..core.extensions import installed as installed_extensions
from ..core.settings import SECRET_NAMES
from ..core.workers import WorkerError
from ..orchestration.script import Script
from ..providers import PROVIDERS
from ..services import EngineServices

log = logging.getLogger(__name__)

# Tauri 在 macOS 上的页面来源是 tauri://localhost；开发时是 Vite 的 1420 端口
ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


class TaskIn(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class Consent(BaseModel):
    confirmed: bool
    statement: str = "我确认拥有该声音的使用授权，或这是我本人的声音。"


class VoiceIn(BaseModel):
    name: str
    audio_path: str
    transcript: str = ""
    language: str = "zh"
    tags: list[str] = Field(default_factory=list)
    consent: Consent


class ProjectIn(BaseModel):
    kind: str = "audiobook"
    title: str
    script: dict[str, Any]


class ProjectPatch(BaseModel):
    title: str | None = None
    script: dict[str, Any] | None = None


class LLMTestIn(BaseModel):
    """测试连接时用界面上还没保存的值；没传的用已保存的设置。"""

    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    thinking: bool | None = None


class SecretIn(BaseModel):
    value: str | None = None


class ProviderIn(BaseModel):
    base_url: str | None = None
    options: dict[str, str] | None = None
    api_key: str | None = None
    """None 表示不修改；空字符串表示删除。"""


def create_app(services: EngineServices, *, run_background: bool = True) -> FastAPI:
    background: list[asyncio.Task] = []

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        recovered = services.tasks.recover()
        if recovered:
            log.info("re-queued %s interrupted tasks", recovered)
        if run_background:
            background.append(asyncio.create_task(services.tasks.run_forever()))
            background.append(asyncio.create_task(services.downloads.run_forever()))
            background.append(asyncio.create_task(_idle_reaper(services)))
        try:
            yield
        finally:
            for t in background:
                t.cancel()
            for t in background:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            await services.downloads.shutdown()
            await services.workers.stop_all()

    app = FastAPI(title="声境引擎", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(request: Request, token: str | None = Query(default=None)) -> None:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else token
        if not supplied or not hmac.compare_digest(supplied, services.token):
            raise HTTPException(status_code=401, detail="令牌无效")

    auth = [Depends(require_token)]

    # ---------- 状态 ----------

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/system", dependencies=auth)
    def system() -> dict[str, Any]:
        return services.system_info()

    @app.get("/catalog", dependencies=auth)
    def catalog_list() -> list[dict[str, Any]]:
        return services.model_list()

    @app.get("/workflows", dependencies=auth)
    def workflows() -> list[dict[str, str]]:
        return services.tasks.workflows()

    @app.post("/workers/{name}/stop", dependencies=auth)
    async def stop_worker(name: str) -> dict[str, Any]:
        if name not in services.workers.specs:
            raise HTTPException(404, "没有这个进程")
        try:
            await services.scheduler.stop_worker(name)
        except WorkerError as exc:
            raise HTTPException(409, str(exc)) from exc
        return services.scheduler.status()

    # ---------- 在线服务商 ----------

    @app.get("/providers", dependencies=auth)
    def list_providers() -> list[dict[str, Any]]:
        return services.provider_list()

    @app.put("/providers/{provider_id}", dependencies=auth)
    def update_provider(provider_id: str, body: ProviderIn) -> list[dict[str, Any]]:
        if provider_id not in PROVIDERS:
            raise HTTPException(404, "没有这个服务商")
        current = services.settings.get().providers.get(provider_id)
        patch: dict[str, Any] = {
            "base_url": body.base_url if body.base_url is not None
            else (current.base_url if current else ""),
            "options": {k: v for k, v in (body.options or {}).items() if v.strip()}
            if body.options is not None else (dict(current.options) if current else {}),
        }
        if not PROVIDERS[provider_id].base_url_editable:
            patch["base_url"] = ""
        providers = {k: v.model_dump() for k, v in services.settings.get().providers.items()}
        providers[provider_id] = patch
        services.settings.update({"providers": providers})
        if body.api_key is not None:
            services.secrets.set(f"{provider_id}_api_key", body.api_key or None)
        return services.provider_list()

    @app.get("/extensions", dependencies=auth)
    def list_extensions() -> dict[str, Any]:
        state = installed_extensions(services.paths.extensions)
        return {name: {"title": title, "installed": state[name]}
                for name, title in EXTENSIONS.items()}

    # ---------- 设置 ----------

    @app.get("/settings", dependencies=auth)
    def get_settings() -> dict[str, Any]:
        return {
            "settings": services.settings.get().model_dump(),
            "secrets": {name: services.secrets.has(name) for name in SECRET_NAMES},
        }

    @app.put("/settings", dependencies=auth)
    def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            services.settings.update(patch)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        services.refresh_hub_env()
        return get_settings()

    @app.put("/secrets/{name}", dependencies=auth)
    def put_secret(name: str, body: SecretIn) -> dict[str, bool]:
        if name not in SECRET_NAMES:
            raise HTTPException(404, "未知密钥")
        services.secrets.set(name, body.value)
        services.refresh_hub_env()
        return {name: services.secrets.has(name)}

    # ---------- LLM ----------

    @app.post("/llm/test", dependencies=auth)
    async def llm_test(body: LLMTestIn) -> dict[str, Any]:
        return await services.test_llm(**body.model_dump(exclude_none=True))

    # ---------- 模型下载 ----------

    @app.get("/downloads", dependencies=auth)
    async def downloads_overview() -> dict[str, Any]:
        # 顺便在后台更新各模型的下载大小（一天查一次）
        services.downloads.refresh_sizes()
        return services.downloads.overview()

    @app.post("/downloads/sizes/refresh", dependencies=auth)
    async def downloads_refresh_sizes() -> dict[str, bool]:
        return {"started": services.downloads.refresh_sizes(max_age=0)}

    @app.post("/downloads/{model_id}", dependencies=auth)
    async def download_start(model_id: str) -> dict[str, Any]:
        _known_model(model_id)
        try:
            return services.downloads.start(model_id).to_dict()
        except DownloadError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/downloads/{model_id}/cancel", dependencies=auth)
    async def download_cancel(model_id: str) -> dict[str, Any]:
        _known_model(model_id)
        try:
            return services.downloads.cancel(model_id).to_dict()
        except DownloadError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/downloads/{model_id}", dependencies=auth)
    async def download_delete(model_id: str) -> dict[str, Any]:
        _known_model(model_id)
        try:
            services.downloads.delete(model_id)
        except DownloadError as exc:
            raise HTTPException(400, str(exc)) from exc
        return services.downloads.status(model_id)

    def _known_model(model_id: str) -> None:
        try:
            catalog.get(model_id)
        except KeyError as exc:
            raise HTTPException(404, "没有这个模型") from exc

    # ---------- 任务 ----------

    @app.get("/tasks", dependencies=auth)
    def list_tasks(limit: int = 100) -> list[dict[str, Any]]:
        return services.tasks.list(limit=limit)

    @app.post("/tasks", dependencies=auth)
    def submit_task(body: TaskIn) -> dict[str, Any]:
        try:
            return services.tasks.submit(body.kind, body.params)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/tasks/{task_id}", dependencies=auth)
    def get_task(task_id: str) -> dict[str, Any]:
        task = services.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "任务不存在")
        return task

    @app.post("/tasks/{task_id}/cancel", dependencies=auth)
    def cancel_task(task_id: str) -> dict[str, Any]:
        try:
            return services.tasks.cancel(task_id)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc

    # ---------- 音色库 ----------

    @app.get("/voices", dependencies=auth)
    def list_voices() -> list[dict[str, Any]]:
        return services.voices.list()

    @app.post("/voices", dependencies=auth)
    def create_voice(body: VoiceIn) -> dict[str, Any]:
        if not body.consent.confirmed:
            raise HTTPException(400, "克隆声音前必须确认拥有该声音的使用授权")
        src = Path(body.audio_path).expanduser()
        if not src.is_file():
            raise HTTPException(400, f"文件不存在：{src}")
        vid = new_id("voice")
        vdir = services.voices.dir / vid
        vdir.mkdir(parents=True, exist_ok=True)
        if services.ffmpeg.available:
            dst = services.ffmpeg.to_wav(src, vdir / "reference.wav", sample_rate=24000)
        else:
            dst = vdir / f"reference{src.suffix.lower()}"
            shutil.copyfile(src, dst)
        voice = services.voices.create(
            voice_id=vid,
            name=body.name,
            audio_path=dst,
            transcript=body.transcript.strip(),
            language=body.language,
            tags=body.tags,
            source="imported",
            consent={**body.consent.model_dump(), "confirmed_at": time.time()},
        )
        task = None
        if not voice["transcript"]:
            task = services.tasks.submit("voice.transcribe", {"voice_id": vid})
        return {"voice": voice, "transcribe_task": task}

    @app.delete("/voices/{voice_id}", dependencies=auth)
    def delete_voice(voice_id: str) -> dict[str, bool]:
        voice = services.voices.get(voice_id)
        if voice is None:
            raise HTTPException(404, "音色不存在")
        services.voices.delete(voice_id)
        shutil.rmtree(services.voices.dir / voice_id, ignore_errors=True)
        return {"ok": True}

    # ---------- 项目 ----------

    @app.get("/projects", dependencies=auth)
    def list_projects(kind: str | None = None) -> list[dict[str, Any]]:
        return services.projects.list(kind)

    @app.post("/projects", dependencies=auth)
    def create_project(body: ProjectIn) -> dict[str, Any]:
        script = _validate_script(body.script)
        return services.projects.create(body.kind, body.title, {"script": script})

    @app.get("/projects/{project_id}", dependencies=auth)
    def get_project(project_id: str) -> dict[str, Any]:
        project = services.projects.get(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        project["renders"] = services.projects.renders(project_id)
        return project

    @app.put("/projects/{project_id}", dependencies=auth)
    def update_project(project_id: str, body: ProjectPatch) -> dict[str, Any]:
        project = services.projects.get(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        data = dict(project["data"])
        if body.script is not None:
            data["script"] = _validate_script(body.script)
        return services.projects.update(project_id, title=body.title, data=data)

    @app.post("/projects/{project_id}/renders/{render_id}/select", dependencies=auth)
    def select_render(project_id: str, render_id: str) -> dict[str, bool]:
        try:
            services.projects.select_render(project_id, render_id)
        except KeyError as exc:
            raise HTTPException(404, "版本不存在") from exc
        return {"ok": True}

    # ---------- 文件 ----------

    @app.get("/files", dependencies=auth)
    def get_file(path: str) -> FileResponse:
        target = Path(path).expanduser().resolve()
        root = services.paths.root.resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "文件不存在或不在数据目录内")
        return FileResponse(target)

    # ---------- 事件 ----------

    @app.websocket("/events")
    async def events(ws: WebSocket, token: str | None = None) -> None:
        if not token or not hmac.compare_digest(token, services.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        queue = services.events.subscribe()

        async def push() -> None:
            while True:
                await ws.send_json(await queue.get())

        sender = asyncio.create_task(push())
        try:
            # 界面不会发消息过来，这里等的是断开：只等事件的话，窗口关掉后这条连接
            # 永远不会结束，引擎退出时也会一直卡在等它
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await sender
            services.events.unsubscribe(queue)

    return app


def _validate_script(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        return Script.model_validate(raw).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, f"剧本格式错误：{exc}") from exc


async def _idle_reaper(services: EngineServices, interval: float = 30.0) -> None:
    """定期关闭空闲的模型进程，把内存还给系统。"""
    while True:
        await asyncio.sleep(interval)
        try:
            stopped = await services.scheduler.stop_idle(services.settings.get().idle_timeout_s)
            if stopped:
                log.info("stopped idle workers: %s", stopped)
        except Exception:  # noqa: BLE001
            log.exception("idle reaper failed")
