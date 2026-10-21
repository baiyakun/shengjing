"""扩展包安装：分析包（PyTorch 分析模型）和音乐包（ACE-Step）。

扩展包是 packaging/build_extension.sh 打出的 tar.gz，顶层目录就是包名
（analysis/ 或 music/），里面是一份独立的 Python 运行环境。
安装位置：<数据目录>/extensions/<包名>/
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

EXTENSIONS = {
    "analysis": "分析扩展包（说话人区分、时间戳、人声分离）",
    "music": "音乐扩展包（ACE-Step 1.5）",
}


class ExtensionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


async def download(url: str, dest: Path, *, progress=None) -> Path:
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0)) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise ExtensionError(f"下载失败：HTTP {resp.status_code}")
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total)
    return dest


def install_archive(name: str, archive: Path, extensions_dir: Path, *, sha256: str | None) -> Path:
    """校验并解压扩展包。先解压到临时目录，确认结构正确后再替换旧版本。"""
    if name not in EXTENSIONS:
        raise ExtensionError(f"未知扩展包：{name}")
    if not archive.is_file():
        raise ExtensionError(f"文件不存在：{archive}")
    if sha256 and sha256_file(archive).lower() != sha256.lower():
        raise ExtensionError("校验值不匹配，文件可能损坏或被篡改")
    extensions_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=extensions_dir) as tmp:
        try:
            with tarfile.open(archive, "r:*") as tar:
                # data 过滤器拒绝绝对路径、指向目录外的链接和设备文件
                tar.extractall(tmp, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise ExtensionError(f"解压失败：{exc}") from exc
        unpacked = Path(tmp) / name
        python = unpacked / "bin" / "python3"
        if not python.exists():
            raise ExtensionError(f"扩展包结构不对：缺少 {name}/bin/python3")
        target = extensions_dir / name
        if target.exists():
            shutil.rmtree(target)
        unpacked.rename(target)
    return target


def installed(extensions_dir: Path) -> dict[str, bool]:
    return {name: (extensions_dir / name / "bin" / "python3").exists() for name in EXTENSIONS}
