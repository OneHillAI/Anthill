"""Containerized remote training over SSH - the mechanism every GPU-host backend shares.

on-prem, AWS, GCP / Azure / IBM all do the same middle step: ship the (already PII-scrubbed) gold to
a Docker + GPU host, run the pinned ``anthill-trainer`` image (``docker run --gpus all``), and fetch
the produced LoRA adapter back. They differ only in **how the host is obtained and torn down**; this
is the identical part in between, so it lives here once and every backend reuses it.

Commands are issued through an injectable ``runner`` (subprocess by default) so the orchestration is
unit-tested without a real host. The live path (a real GPU box) is owner-validated, like Modal.

Requirements on the host: SSH (key auth), Docker, and an NVIDIA container runtime. Teardown of the
host is the CALLER's job (on-prem: none; cloud backends: the provisioner's try/finally + reaper).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .backends.base import BackendError

# The trainer image every VM/box backend runs. Override per-deployment via the env var.
DEFAULT_TRAINER_IMAGE = os.environ.get(
    "ANTHILL_TRAINER_IMAGE", "ghcr.io/onehillai/anthill-trainer:latest"
)

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class SSHHost:
    """An SSH-reachable Docker + GPU host."""

    host: str  # "user@host" or "host"
    key_path: str = ""  # private key path; "" = default agent / ~/.ssh keys
    port: int = 22
    workdir: str = "/tmp/anthill-train"


def parse_endpoint(endpoint: str) -> SSHHost:
    """Turn a ``training_gpu_endpoint`` ("ssh user@box", "user@box", "user@box:2222") into an SSHHost.
    The private key comes from ``ANTHILL_TRAINING_SSH_KEY`` (else default agent/keys)."""
    e = (endpoint or "").strip()
    if e.lower().startswith("ssh "):
        e = e[4:].strip()
    port = 22
    if "@" in e and ":" in e.rsplit("@", 1)[-1]:
        hostpart, _, p = e.rpartition(":")
        if p.isdigit():
            e, port = hostpart, int(p)
    return SSHHost(host=e, key_path=os.environ.get("ANTHILL_TRAINING_SSH_KEY", ""), port=port)


def _default_runner(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _run(
    runner: Runner, args: list[str], *, timeout: int | None = None
) -> subprocess.CompletedProcess:
    cp = runner(args, timeout=timeout)
    if cp.returncode != 0:
        err = (cp.stderr or "")[-600:] if getattr(cp, "stderr", None) else f"exit {cp.returncode}"
        raise BackendError(f"remote step failed ({' '.join(args[:2])} ...): {err}")
    return cp


def _ssh(h: SSHHost) -> list[str]:
    base = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        str(h.port),
    ]
    return base + (["-i", h.key_path] if h.key_path else [])


def _scp(h: SSHHost) -> list[str]:
    base = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-P",
        str(h.port),
    ]
    return base + (["-i", h.key_path] if h.key_path else [])


def containerized_train(
    h: SSHHost,
    *,
    dataset_path: str,
    base_model: str,
    image: str = DEFAULT_TRAINER_IMAGE,
    runner: Runner | None = None,
    step_timeout: int = 60 * 60 * 6,
) -> str:
    """Ship gold -> ``docker run --gpus all <image> anthill train-adapter ...`` -> fetch the adapter.

    Returns the LOCAL path to the fetched adapter directory. Raises ``BackendError`` on any failed
    step. Only the already-scrubbed gold is transmitted; the image carries the toolchain + code."""
    runner = runner or _default_runner
    wd = shlex.quote(h.workdir)
    # 1. workdir + ship the scrubbed gold
    _run(runner, [*_ssh(h), h.host, f"mkdir -p {wd}/in {wd}/out"])
    _run(runner, [*_scp(h), dataset_path, f"{h.host}:{h.workdir}/in/gold.jsonl"])
    # 2. run the trainer container (gold in -> adapter out)
    docker_cmd = (
        "docker run --rm --gpus all "
        f"-v {wd}/in:/in:ro -v {wd}/out:/out {shlex.quote(image)} "
        f"anthill train-adapter --dataset /in/gold.jsonl --base {shlex.quote(base_model)} "
        "--out /out/adapter"
    )
    _run(runner, [*_ssh(h), h.host, docker_cmd], timeout=step_timeout)
    # 3. fetch the adapter back
    local = tempfile.mkdtemp(prefix="anthill-adapter-")
    _run(runner, [*_scp(h), "-r", f"{h.host}:{h.workdir}/out/adapter", local])
    adapter = str(Path(local) / "adapter")
    if not Path(adapter).exists():
        raise BackendError("remote training finished but no adapter was fetched")
    return adapter
