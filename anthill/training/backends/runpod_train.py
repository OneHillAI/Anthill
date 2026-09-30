"""Live RunPod training execution: a fine-tune on a GPU **pod** rented from the org's own RunPod account.

RunPod has no "run this function" primitive like Modal, so a training job is a **pod** (an on-demand GPU
instance). A pod exposes SSH + Docker, which is exactly what the shared ``training.remote`` trainer needs,
so the work composes:

    launch a GPU pod  ->  wait until it is SSH-reachable  ->  containerized_train() over that SSH host
    ->  fetch the LoRA adapter back  ->  **terminate the pod** (guaranteed, success or failure)

Only the pod lifecycle (launch / ssh-target / terminate) is new; the actual ship-gold -> train -> fetch
step is the same ``containerized_train`` every GPU-host backend already uses (and which is unit-tested).
The orchestration here - cost cap, poll-to-ready, and a try/finally that **always tears the pod down** so
a crashed run never leaves a billable GPU - is fully unit-tested behind an injectable ``RunpodPodClient``
and ``train_fn`` (no network, no live account, no spend).

``_RealRunpodPodClient`` is the only part that touches RunPod (and real money). It drives the RunPod
**pod** GraphQL API over httpx (the same Bearer key the serving provisioner uses, ie the org's cloud
account). NEEDS LIVE VALIDATION against a real account: the exact pod input fields, the SSH port shape in
``runtime.ports``, and whether the chosen pod image runs the trainer are the bits to confirm on a first
real run (isolated here, like the serving provisioner was).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..remote import SSHHost, containerized_train
from .base import BackendError

_RUNPOD_GRAPHQL = "https://api.runpod.io/graphql"
# A broadly-available RunPod pod GPU id; tune per model size on first live run.
_DEFAULT_GPU_TYPE = "NVIDIA A40"
# The pod image: the pinned anthill trainer (toolchain + the `anthill` CLI). containerized_train runs the
# trainer container on the pod, so the pod needs Docker + an NVIDIA runtime (a RunPod pytorch/base image).
_DEFAULT_POD_IMAGE = os.environ.get(
    "ANTHILL_RUNPOD_POD_IMAGE", "runpod/pytorch:2.2.0-py3.10-cuda12.1.1"
)
_DEFAULT_DISK_GB = 40
_DEFAULT_READY_ATTEMPTS = 60  # times to poll for the pod to become SSH-reachable

# Every training pod's name starts with this, so the reaper (training/reaper.py) can find the account's
# training pods by name and terminate a leaked one. Keep it in sync with the name built in `train()`.
TRAIN_POD_PREFIX = "anthill-train-"


@dataclass
class PodInfo:
    """One pod on the account, as the reaper needs to see it. ``age_minutes`` is the pod's running
    uptime (0 when it is not yet running, so a just-launched pod reads as young and is never reaped)."""

    id: str
    name: str
    age_minutes: float
    desired_status: str = ""


# Rough RunPod on-demand $/hr by GPU (display-only cost guardrail; never billed against).
_GPU_USD_PER_HR = {"NVIDIA A40": 0.39, "NVIDIA A100 80GB": 1.19, "NVIDIA RTX A6000": 0.49}


def estimate_cost(gpu_type: str, minutes: int) -> float:
    return round(_GPU_USD_PER_HR.get(gpu_type, 1.0) * (max(0, minutes) / 60.0), 2)


@runtime_checkable
class RunpodPodClient(Protocol):
    """The thin RunPod pod surface the trainer needs. ``_RealRunpodPodClient`` calls the API; tests
    pass a fake. Keeping it tiny is what makes the orchestration testable without a live account."""

    def launch(self, *, name: str, gpu_type: str, image: str, disk_gb: int) -> str:
        """Launch an on-demand GPU pod; return its id."""
        ...

    def ssh_host(self, pod_id: str) -> SSHHost | None:
        """The pod's SSH target once it is reachable, or None while it is still coming up."""
        ...

    def terminate(self, pod_id: str) -> None:
        """Terminate the pod (idempotent; must not raise if already gone)."""
        ...

    def list_pods(self) -> list[PodInfo]:
        """Every pod on the account, so the reaper can find leaked ``anthill-train-*`` pods by name."""
        ...


# (ssh_host, *, dataset_path, base_model) -> local adapter path. Defaults to the shared SSH trainer.
TrainFn = Callable[..., str]


class RunpodTrainer:
    """Run one LoRA fine-tune on an ephemeral RunPod pod and return the fetched adapter."""

    def train(
        self,
        *,
        dataset_path: str,
        base_model: str,
        api_key: str = "",
        gpu_type: str = _DEFAULT_GPU_TYPE,
        image: str = _DEFAULT_POD_IMAGE,
        disk_gb: int = _DEFAULT_DISK_GB,
        max_runtime_min: int = 120,
        cost_cap_usd: float = 25.0,
        client: RunpodPodClient | None = None,
        train_fn: TrainFn | None = None,
        sleep: Callable[[float], None] | None = None,
        ready_attempts: int = _DEFAULT_READY_ATTEMPTS,
    ) -> str:
        """Launch a pod, train on it, fetch the adapter, and ALWAYS terminate the pod.

        ``client`` defaults to the real RunPod pod client built from ``api_key`` (the org's cloud RunPod
        key); tests inject a fake. ``train_fn`` defaults to the shared ``containerized_train`` (ship the
        already-scrubbed gold, run the trainer container, fetch the adapter); tests inject a fake. The
        cost cap refuses before launching; the try/finally guarantees teardown so a failure never leaves
        a billable pod. Returns the LOCAL adapter path; raises ``BackendError`` on refusal or failure.
        """
        est = estimate_cost(gpu_type, max_runtime_min)
        if est > cost_cap_usd:
            raise BackendError(
                f"Estimated worst-case cost ${est} exceeds the ${cost_cap_usd} per-run cap - "
                f"lower the runtime or raise the cap."
            )
        if not (dataset_path or "").strip():
            raise BackendError("No dataset to train on.")
        if client is None:
            client = _RealRunpodPodClient(api_key)
        do_train = train_fn or containerized_train
        do_sleep = sleep or (lambda _s: None)

        name = f"{TRAIN_POD_PREFIX}{base_model}".replace(":", "-").replace("/", "-").lower()[:60]
        pod_id = client.launch(name=name, gpu_type=gpu_type, image=image, disk_gb=disk_gb)
        # From here a pod exists and is billable: guarantee teardown on every path.
        try:
            host: SSHHost | None = None
            for _ in range(max(1, ready_attempts)):
                host = client.ssh_host(pod_id)
                if host:
                    break
                do_sleep(5)
            if host is None:
                raise BackendError("RunPod pod did not become SSH-reachable in time")
            return do_train(host, dataset_path=dataset_path, base_model=base_model)
        finally:
            client.terminate(pod_id)  # ephemeral: torn down on success and failure alike


class _RealRunpodPodClient:
    """Drives the RunPod **pod** GraphQL API over httpx (Bearer auth: the org's cloud RunPod key).
    NEEDS LIVE VALIDATION against a real account (spends money). The pod input fields, the SSH port
    shape, and the pod image are the bits to confirm on the first real run."""

    def __init__(self, api_key: str):
        if not (api_key or "").strip():
            raise BackendError("A RunPod API key is required to train (none configured).")
        self._api_key = api_key.strip()

    def _gql(self, query: str) -> dict:
        import httpx

        r = httpx.post(
            _RUNPOD_GRAPHQL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            json={"query": query},
            timeout=30,
        )
        if r.status_code == 401:
            raise BackendError("RunPod rejected the API key (unauthorized).")
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise BackendError(f"RunPod API error: {body['errors'][0].get('message', 'unknown')}")
        return body.get("data") or {}

    def _q(self, s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')

    def launch(self, *, name: str, gpu_type: str, image: str, disk_gb: int) -> str:
        data = self._gql(
            f"""mutation {{ podFindAndDeployOnDemand(input: {{
                cloudType: SECURE, gpuCount: 1, gpuTypeId: "{self._q(gpu_type)}",
                name: "{self._q(name)}", imageName: "{self._q(image)}",
                containerDiskInGb: {int(disk_gb)}, volumeInGb: 0, ports: "22/tcp",
                startSsh: true
            }}) {{ id }} }}"""
        )
        pod_id = (data.get("podFindAndDeployOnDemand") or {}).get("id")
        if not pod_id:
            raise BackendError("RunPod did not return a pod id.")
        return pod_id

    def ssh_host(self, pod_id: str) -> SSHHost | None:
        data = self._gql(
            f'query {{ pod(input: {{podId: "{self._q(pod_id)}"}}) {{ '
            "runtime { ports { ip publicPort privatePort type } } } }"
        )
        runtime = (data.get("pod") or {}).get("runtime") or {}
        for port in runtime.get("ports") or []:
            if int(port.get("privatePort") or 0) == 22 and (port.get("type") or "") == "tcp":
                ip, pub = port.get("ip"), port.get("publicPort")
                if ip and pub:
                    return SSHHost(
                        host=f"root@{ip}",
                        key_path=os.environ.get("ANTHILL_TRAINING_SSH_KEY", ""),
                        port=int(pub),
                    )
        return None  # not reachable yet

    def terminate(self, pod_id: str) -> None:
        try:
            self._gql(f'mutation {{ podTerminate(input: {{podId: "{self._q(pod_id)}"}}) }}')
        except Exception:
            pass  # idempotent teardown: never raise

    def list_pods(self) -> list[PodInfo]:
        """Every pod on the account. ``runtime.uptimeInSeconds`` is present only while a pod is running,
        so a pod still provisioning reads as age 0 (young) and the reaper leaves it alone."""
        data = self._gql(
            "query { myself { pods { id name desiredStatus runtime { uptimeInSeconds } } } }"
        )
        pods = ((data.get("myself") or {}).get("pods")) or []
        out: list[PodInfo] = []
        for p in pods:
            pid = p.get("id")
            if not pid:
                continue
            up = (p.get("runtime") or {}).get("uptimeInSeconds")
            try:
                age_min = float(up) / 60.0 if up is not None else 0.0
            except (TypeError, ValueError):
                age_min = 0.0
            out.append(
                PodInfo(
                    id=pid,
                    name=p.get("name") or "",
                    age_minutes=age_min,
                    desired_status=p.get("desiredStatus") or "",
                )
            )
        return out
