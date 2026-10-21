"""音乐生成：调用 ACE-Step 1.5 自带的 API 服务。

接口（见 ACE-Step 文档 docs/en/API.md）：
POST /release_task 提交 → POST /query_result 轮询（status 1 成功、2 失败）
→ GET /v1/audio?path=... 下载。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..core.tasks import TaskContext, UserFacingError
from ..orchestration.llm import chat_json
from ..orchestration.prompts import SongDraft, song_messages
from .common import llm_session


class AceStepError(RuntimeError):
    pass


class AceStepClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=10.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _data(resp: httpx.Response) -> Any:
        try:
            body = resp.json()
        except ValueError as exc:
            raise AceStepError(f"ACE-Step 返回无法解析：HTTP {resp.status_code}") from exc
        if resp.status_code >= 400 or body.get("code", 200) != 200 or body.get("error"):
            raise AceStepError(f"ACE-Step 错误：{body.get('error') or body.get('detail')}")
        return body.get("data")

    async def release_task(self, payload: dict[str, Any]) -> str:
        data = self._data(await self._http.post("/release_task", json=payload))
        return str(data["task_id"])

    async def query(self, task_id: str) -> dict[str, Any]:
        data = self._data(await self._http.post("/query_result", json={"task_id_list": [task_id]}))
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if str(entry.get("task_id", task_id)) == task_id:
                return entry
        raise AceStepError(f"查询不到任务 {task_id}")

    async def wait(self, task_id: str, *, poll: float = 2.0, timeout: float = 1800.0) -> list[str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            entry = await self.query(task_id)
            status = int(entry.get("status", 0))
            if status == 1:
                return _audio_files(entry.get("result"))
            if status == 2:
                raise AceStepError(f"生成失败：{entry.get('result') or entry.get('error')}")
            await asyncio.sleep(poll)
        raise AceStepError("生成超时")

    async def download(self, file_url: str, out: Path) -> Path:
        # file 字段形如 /v1/audio?path=%2Ftmp%2Fapi_audio%2Fabc.wav，也可能直接是路径
        query = parse_qs(urlparse(file_url).query)
        path = query.get("path", [file_url])[0]
        resp = await self._http.get("/v1/audio", params={"path": path})
        if resp.status_code >= 400:
            raise AceStepError(f"下载失败：HTTP {resp.status_code}")
        out.write_bytes(resp.content)
        return out


def _audio_files(result: Any) -> list[str]:
    """result 字段是 JSON 字符串，里面的 file 是音频地址。兼容单个对象和列表两种写法。"""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return []
    items = result if isinstance(result, list) else [result]
    files = []
    for item in items:
        if isinstance(item, dict) and item.get("file"):
            files.append(str(item["file"]))
    return files


async def music_generate(ctx: TaskContext) -> dict[str, Any]:
    p = ctx.params
    services = ctx.services
    spec = services.model_for("music", p.get("model"))
    services.require_worker(spec)

    style = (p.get("style") or "").strip()
    lyrics = p.get("lyrics") or ""
    title = p.get("title") or ""
    language = p.get("language") or "zh"
    draft = None
    if p.get("theme") and not lyrics and not p.get("instrumental"):
        ctx.progress(0.02, "LLM 写歌词")
        async with llm_session(services) as llm:
            draft = await chat_json(llm, song_messages(p["theme"], language), SongDraft)
        style = style or draft.style
        lyrics = draft.lyrics
        title = title or draft.title
    if not style:
        raise UserFacingError("请填写音乐风格描述，或给一个主题让 LLM 来写")
    if p.get("instrumental"):
        lyrics = ""

    payload: dict[str, Any] = {
        "prompt": style,
        "lyrics": lyrics,
        "audio_duration": float(p.get("duration") or 60),
        "vocal_language": language,
        "batch_size": max(1, min(int(p.get("batch_size") or 2), 8)),
        "audio_format": "wav",
        "thinking": bool(p.get("thinking", False)),
        "task_type": "text2music",
    }
    bpm = p.get("bpm") or (draft.bpm if draft else None)
    key = p.get("key_scale") or (draft.key_scale if draft else None)
    if bpm:
        payload["bpm"] = int(bpm)
    if key:
        payload["key_scale"] = key

    ctx.progress(0.1, "加载 ACE-Step")
    async with services.scheduler.use(spec.id) as handle:
        client = AceStepClient(handle.base_url, api_key=handle.token)
        try:
            task_id = await client.release_task(payload)
            ctx.progress(0.2, "生成中")
            files = await client.wait(task_id)
            outputs = []
            for i, url in enumerate(files):
                ctx.check_cancelled()
                out = await client.download(url, ctx.out_dir / f"music_{i + 1:02d}.wav")
                outputs.append(str(out))
        except AceStepError as exc:
            raise UserFacingError(str(exc)) from exc
        finally:
            await client.aclose()
    return {"title": title, "style": style, "lyrics": lyrics, "files": outputs, "model": spec.id}
