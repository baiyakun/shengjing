"""机器内存检测与内存预算。"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

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


def chip_name() -> str:
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
