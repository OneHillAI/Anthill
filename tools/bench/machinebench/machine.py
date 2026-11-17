"""What machine a result came from: chip, GPU cores, memory, system, power, and the swap counter."""

from __future__ import annotations

import platform
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Machine:
    id: str  # for the file name, e.g. apple-m4-16gb
    chip: str
    gpu_cores: int | None
    memory_gb: float
    system: str
    bandwidth_gbps: float | None  # Anthill's table, for reference; None when the chip is not in it
    power: str

    def as_dict(self) -> dict:
        return asdict(self)


def machine_id(chip: str, memory_gb: float) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", chip.lower()).strip("-") or "unknown"
    return f"{slug}-{round(memory_gb)}gb"


def _sysctl(name: str, run: Runner) -> str:
    try:
        out = run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _memory_gb(run: Runner) -> float:
    raw = _sysctl("hw.memsize", run)
    if raw.isdigit():
        return int(raw) / 2**30
    try:  # Linux
        kb = re.search(r"MemTotal:\s+(\d+) kB", Path("/proc/meminfo").read_text())
    except OSError:
        return 0.0
    return int(kb.group(1)) / 2**20 if kb else 0.0


def describe(power: str, run: Runner = subprocess.run) -> Machine:
    """This machine. Chip name and GPU cores come from the same probes the model picker uses."""
    from anthill.hosting import sizing

    chip = _sysctl("machdep.cpu.brand_string", run) or platform.processor() or platform.machine()
    memory = _memory_gb(run)
    return Machine(
        id=machine_id(chip, memory),
        chip=chip,
        gpu_cores=sizing._apple_gpu_cores(),
        memory_gb=round(memory, 1),
        system=f"{platform.system()} {platform.release()} {platform.machine()}",
        bandwidth_gbps=sizing._apple_chip_bandwidth_gbps(),
        power=power,
    )


def swap_outs(run: Runner = subprocess.run, proc_vmstat: Path = Path("/proc/vmstat")) -> int | None:
    """Pages swapped out since boot (macOS ``vm_stat``, Linux ``/proc/vmstat``), or None when it cannot be read.
    The difference over a run says whether the machine swapped while the model ran."""
    try:
        out = run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    found = re.search(r"Swapouts:\s+(\d+)", out)
    if found:
        return int(found.group(1))
    try:
        found = re.search(r"^pswpout (\d+)$", proc_vmstat.read_text(), re.M)
    except OSError:
        return None
    return int(found.group(1)) if found else None
