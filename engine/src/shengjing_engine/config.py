"""运行配置与数据目录。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR_NAME = "Shengjing"


def default_data_dir() -> Path:
    """返回默认数据目录。

    macOS: ~/Library/Application Support/Shengjing
    其他平台（仅用于开发和测试）: ~/.local/share/shengjing
    """
    override = os.environ.get("SHENGJING_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME.lower()


@dataclass(frozen=True)
class DataPaths:
    """数据目录下的固定子目录。"""

    root: Path

    @property
    def db(self) -> Path:
        return self.root / "shengjing.sqlite3"

    @property
    def settings(self) -> Path:
        return self.root / "settings.json"

    @property
    def voices(self) -> Path:
        return self.root / "voices"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def hf_home(self) -> Path:
        """模型下载缓存（传给各模型进程的 HF_HOME）。"""
        return self.root / "models"

    @property
    def extensions(self) -> Path:
        """扩展包（分析包、音乐包）的安装目录。"""
        return self.root / "extensions"

    def ensure(self) -> None:
        for path in (
            self.root,
            self.voices,
            self.projects,
            self.outputs,
            self.tmp,
            self.logs,
            self.hf_home,
            self.extensions,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class RuntimePaths:
    """各运行环境的可执行文件。

    打包后的 App 由 Tauri 外壳通过环境变量传入；开发时可以手动设置。
    没有安装的扩展包为 None，对应功能在界面上显示为“需要安装扩展包”。
    """

    base_python: str = field(default_factory=lambda: sys.executable)
    analysis_python: str | None = None
    acestep_command: tuple[str, ...] | None = None
    ffmpeg: str | None = None

    @classmethod
    def from_env(cls, paths: DataPaths | None = None) -> RuntimePaths:
        analysis = os.environ.get("SHENGJING_PY_ANALYSIS")
        if not analysis and paths is not None:
            candidate = paths.extensions / "analysis" / "bin" / "python3"
            if candidate.exists():
                analysis = str(candidate)

        music_python = os.environ.get("SHENGJING_PY_MUSIC")
        if not music_python and paths is not None:
            candidate = paths.extensions / "music" / "bin" / "python3"
            if candidate.exists():
                music_python = str(candidate)

        return cls(
            base_python=os.environ.get("SHENGJING_PY_BASE") or sys.executable,
            analysis_python=analysis or None,
            acestep_command=(music_python, "-m", "acestep.api_server") if music_python else None,
            ffmpeg=os.environ.get("SHENGJING_FFMPEG") or None,
        )
