"""安装扩展包：从地址下载（或使用本地文件），校验后解压。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..core import extensions
from ..core.tasks import TaskContext, UserFacingError


async def install_extension(ctx: TaskContext) -> dict[str, Any]:
    p = ctx.params
    name = p.get("name") or ""
    if name not in extensions.EXTENSIONS:
        raise UserFacingError(f"未知扩展包：{name}")
    services = ctx.services
    if p.get("archive_path"):
        archive = Path(p["archive_path"]).expanduser()
    elif p.get("url"):
        if not p.get("sha256"):
            raise UserFacingError("从网络安装扩展包必须提供 sha256 校验值")
        archive = ctx.out_dir / f"{name}.tar.gz"
        ctx.progress(0.01, "下载中")
        try:
            await extensions.download(
                p["url"], archive, progress=lambda f: ctx.progress(0.9 * f, "下载中")
            )
        except extensions.ExtensionError as exc:
            raise UserFacingError(str(exc)) from exc
    else:
        raise UserFacingError("请提供扩展包文件或下载地址")
    ctx.progress(0.92, "校验并解压")
    try:
        target = await asyncio.to_thread(
            extensions.install_archive,
            name,
            archive,
            services.paths.extensions,
            sha256=p.get("sha256"),
        )
    except extensions.ExtensionError as exc:
        raise UserFacingError(str(exc)) from exc
    services.refresh_runtime()
    return {"name": name, "path": str(target), "installed": extensions.installed(
        services.paths.extensions)}
