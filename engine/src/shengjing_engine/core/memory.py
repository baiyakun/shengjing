"""机器内存检测与内存预算。"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from functools import cache

GIB = 1024**3


def total_memory_gb() -> float:
    """物理内存总量（GB）。Apple Silicon 上就是 CPU 和 GPU 共用的统一内存。"""
    override = os.environ.get("SHENGJING_TOTAL_MEMORY_GB")
    if override:
        return float(override)
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
            ).stdout
            return int(out.strip()) / GIB
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024 / GIB
    except OSError:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIB
    except (ValueError, OSError, AttributeError):
        return 16.0


@cache
def chip_name() -> str:
    """芯片型号，例如 Apple M2 Pro。SHENGJING_CHIP 可以覆盖（测试用）。"""
    override = os.environ.get("SHENGJING_CHIP")
    if override:
        return override
    if sys.platform == "darwin":
        try:
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return "unknown"


@dataclass(frozen=True)
class LocalSupport:
    """这台机器能不能在本机运行模型（MLX 只能在 Apple 芯片上运行）。"""

    supported: bool
    arch: str
    """当前进程的架构：arm64 或 x86_64。"""
    apple_silicon: bool
    """硬件是不是 Apple 芯片（Intel 版本通过 Rosetta 运行时，arch 是 x86_64，这里仍是 True）。"""
    reason: str = ""

    def to_dict(self) -> dict:
        return {"supported": self.supported, "arch": self.arch,
                "apple_silicon": self.apple_silicon, "reason": self.reason}


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


@cache
def local_support() -> LocalSupport:
    """Intel 芯片的 Mac 不能运行本地模型，只能用在线 API。

    只在 macOS 上判断；其他系统只用于开发和测试，按支持处理。
    SHENGJING_LOCAL_MODELS=0/1 可以覆盖（测试和调试界面用）。
    """
    arch = platform.machine()
    is_mac = sys.platform == "darwin"
    apple_silicon = arch == "arm64" or (is_mac and _sysctl("hw.optional.arm64") == "1")
    override = os.environ.get("SHENGJING_LOCAL_MODELS")
    if override in ("0", "1"):
        supported = override == "1"
    else:
        supported = not is_mac or arch == "arm64"
    reason = ""
    if not supported:
        if apple_silicon and arch != "arm64":
            reason = ("当前运行的是 Intel 版本。这台 Mac 是 Apple 芯片，"
                      "安装 Apple 芯片版本后就能在本机运行模型")
        else:
            reason = "这台 Mac 使用 Intel 芯片，不能在本机运行模型，只能使用在线 API"
    return LocalSupport(supported=supported, arch=arch, apple_silicon=apple_silicon,
                        reason=reason)


def memory_tier(total_gb: float) -> int:
    """把内存归到 16 / 32 / 64 / 128 四档，用于选默认模型组合。"""
    for tier in (128, 64, 32):
        if total_gb >= tier * 0.95:
            return tier
    return 16


@dataclass(frozen=True)
class MemoryBudget:
    total_gb: float
    reserve_gb: float

    @property
    def budget_gb(self) -> float:
        """留给模型进程的内存。"""
        return max(0.0, self.total_gb - self.reserve_gb)

    @classmethod
    def detect(cls, reserve_gb: float | None = None) -> MemoryBudget:
        total = total_memory_gb()
        if reserve_gb is None:
            # 给系统、界面和主引擎留出余量：至少 4GB，或总内存的 20%
            reserve_gb = max(4.0, total * 0.2)
        return cls(total_gb=total, reserve_gb=reserve_gb)
