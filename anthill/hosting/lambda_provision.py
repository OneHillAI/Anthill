"""Live Lambda Labs provisioning - the primary Cloud VPC provider.

Unlike RunPod's managed serverless endpoint, Lambda gives an always-on **GPU VM**, so the "connection"
is the instance lifecycle: launch a GPU instance, wait for it to boot and get a public IP, and it
serves the model via vLLM (started by a launch-time startup script) at ``http://<ip>:8000/v1``;
teardown terminates it. One always-on instance hosts the model + training + the wiki together.

Behind an injectable ``LambdaClient`` so the orchestration (launch -> active -> endpoint, with a
**guaranteed terminate on failure** so a failed run never leaves a billable VM) is fully unit-tested
without the API or a live account. ``_RealLambdaClient`` hits the Lambda Cloud API over httpx (Basic
auth, the API key as the username).

NEEDS LIVE VALIDATION against a real Lambda account (spends money). The launch config Lambda requires -
a registered **SSH key name**, the **instance type**, and the **region** - is set by the admin under
Settings -> Organization and threaded in via ``provision_run`` (SSH keys / instance type fall back to
``LAMBDA_SSH_KEY_NAMES`` and the default below only when left blank). The one bit still to confirm on a
first live run, isolated here, is the **serving bootstrap**: whether Lambda runs the startup script and
vLLM comes up on :8000.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from . import secure_tunnel, sizing
from .endpoint import OrgEndpoint
from .provision import LambdaProvisioner as _LambdaPlanner
from .provision import ProvisionError, ProvisionResult, ProvisionSpec, endpoint_is_secure

_BASE = "https://cloud.lambdalabs.com/api/v1"
_VLLM_PORT = 8000
_TUNNEL_USER = "ubuntu"  # Lambda's default AMI user
_DEFAULT_REGION = "us-west-1"
_DEFAULT_INSTANCE_TYPE = "gpu_1x_a10"  # a small, broadly-available GPU; tune per model on first run
_DEFAULT_READY_ATTEMPTS = 60
# Lambda's real instance-type naming convention (e.g. "gpu_8x_h100") encodes the GPU count in the SKU
# name itself - every instance type shaped this way is one of Lambda's genuine NVLink/NVSwitch multi-GPU
# nodes, never a PCIe-spread box, so deriving gpu_count from it also satisfies the spec's "genuine
# multi-GPU node with a fast interconnect" requirement by construction (docs/specs/
# multi-gpu-tensor-parallel-serving.md). No separate admin-facing gpu_count field or SKU table needed.
_GPU_COUNT_RE = re.compile(r"^gpu_(\d+)x_")


def gpu_count_from_instance_type(instance_type: str) -> int:
    """The tensor-parallel size implied by a Lambda instance-type string, e.g. ``"gpu_8x_h100"`` -> 8.
    1 (single GPU, no tensor-parallel) for a blank string or any shape that doesn't match."""
    m = _GPU_COUNT_RE.match((instance_type or "").strip())
    return int(m.group(1)) if m else 1


def _attention_heads_for_model(model_ref: str) -> int:
    """The catalog's known attention-head count for ``model_ref`` (a display name, HF id, or Ollama tag -
    ``spec.model`` is already resolved to an id/tag by the time it reaches here), or 0 if the model is
    not in the catalog or its head count is uncurated. 0 is treated as "not checked", never as a refusal
    - see ``sizing.gpu_count_divides_heads``."""
    for m in sizing.load_catalog():
        if model_ref in (m.name, m.hf_id, m.ollama_tag):
            return m.num_attention_heads
    return 0


def vllm_startup_script(
    model: str, api_key: str, *, authorized_keys_line: str = "", gpu_count: int = 1
) -> str:
    """A cloud-init/bash startup script that serves ``model`` with the vLLM OpenAI server on :8000,
    protected by ``api_key`` (clients must send ``Authorization: Bearer <key>``).

    Binds vLLM to loopback only (``127.0.0.1:8000``) - the port is never reachable from the internet,
    only from a process on the VM itself. ``authorized_keys_line`` (when given) is appended to the
    default user's ``authorized_keys`` in the same script, so Anthill's SSH tunnel (Option A,
    docs/specs/llm-endpoint-secure-transport.md) can reach that loopback port with no separate Lambda API
    call or registered-key-name step; it is independent of any admin-registered ``ssh_key_names``.

    ``gpu_count`` > 1 passes vLLM's ``--tensor-parallel-size`` (docs/specs/
    multi-gpu-tensor-parallel-serving.md), splitting the model across every GPU ``--gpus all`` already
    exposes; omitted (no behavior change) when ``gpu_count`` is 1.

    Passed at launch so the VM comes up serving without a separate SSH step. Whether Lambda runs it is
    the serving-bootstrap gap to confirm live; on a bare VM you would otherwise run this over SSH.
    """
    safe = model.replace("'", "")
    key = (api_key or "").replace("'", "")
    key_step = ""
    if authorized_keys_line:
        key_step = (
            "mkdir -p /home/ubuntu/.ssh\n"
            f"echo {shlex.quote(authorized_keys_line)} >> /home/ubuntu/.ssh/authorized_keys\n"
            "chmod 700 /home/ubuntu/.ssh\n"
            "chmod 600 /home/ubuntu/.ssh/authorized_keys\n"
            "chown -R ubuntu:ubuntu /home/ubuntu/.ssh\n"
        )
    tp_flag = f" --tensor-parallel-size {gpu_count}" if gpu_count > 1 else ""
    return (
        "#!/usr/bin/env bash\n"
        "set -e\n"
        f"{key_step}"
        "docker run -d --gpus all -p 127.0.0.1:8000:8000 --restart unless-stopped "
        f"vllm/vllm-openai:latest --model '{safe}' --port 8000 --api-key '{key}'{tp_flag}\n"
    )


@dataclass(frozen=True)
class LambdaLaunch:
    instance_id: str
    ip: str  # public IP once the instance is active ("" while still booting)


@runtime_checkable
class LambdaClient(Protocol):
    """The thin Lambda Cloud API surface the orchestrator needs. ``_RealLambdaClient`` calls the API;
    tests pass a fake."""

    def launch(
        self,
        *,
        region: str,
        instance_type: str,
        ssh_key_names: list[str],
        startup_script: str,
        name: str,
    ) -> str:
        """Launch a GPU instance; return its id."""
        ...

    def instance(self, instance_id: str) -> tuple[str, str]:
        """(status, public_ip) for an instance; ip is "" until active."""
        ...

    def terminate(self, instance_id: str) -> None:
        """Terminate the instance (idempotent; must not raise if already gone)."""
        ...


class _RealLambdaClient:
    """Calls the Lambda Cloud API over httpx (Basic auth: the API key is the username). NEEDS LIVE
    VALIDATION against a real account (spends money)."""

    def __init__(self, api_key: str):
        if not (api_key or "").strip():
            raise ProvisionError(
                "A Lambda Cloud API key is required to provision (none configured)."
            )
        self._auth = (api_key.strip(), "")

    def _req(self, method: str, path: str, payload: dict | None = None) -> dict:
        import httpx

        r = httpx.request(method, f"{_BASE}{path}", auth=self._auth, json=payload, timeout=30)
        if r.status_code == 401:
            raise ProvisionError("Lambda rejected the API key (unauthorized).")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            raise ProvisionError(f"Lambda API error ({r.status_code}): {msg}")
        return (r.json() or {}).get("data") or {}

    def launch(
        self,
        *,
        region: str,
        instance_type: str,
        ssh_key_names: list[str],
        startup_script: str,
        name: str,
    ) -> str:
        data = self._req(
            "POST",
            "/instance-operations/launch",
            {
                "region_name": region,
                "instance_type_name": instance_type,
                "ssh_key_names": ssh_key_names,
                "name": name,
                "user_data": startup_script,  # serving bootstrap (confirm Lambda honors it on first run)
            },
        )
        ids = data.get("instance_ids") or []
        if not ids:
            raise ProvisionError("Lambda did not return an instance id.")
        return ids[0]

    def instance(self, instance_id: str) -> tuple[str, str]:
        data = self._req("GET", f"/instances/{instance_id}")
        return (data.get("status") or "", data.get("ip") or "")

    def terminate(self, instance_id: str) -> None:
        try:
            self._req("POST", "/instance-operations/terminate", {"instance_ids": [instance_id]})
        except Exception:
            pass  # idempotent teardown: never raise


class LambdaLiveProvisioner(_LambdaPlanner):
    """Lambda provisioner with a real ``provision``/``teardown`` (the planner ``plan`` is inherited)."""

    def available(self) -> tuple[bool, str]:
        return (
            True,
            "Lambda Labs provisioning is wired up. Add your Lambda Cloud API key under Settings -> "
            "Organization, then provision; the GPU instance runs until you tear it down.",
        )

    def provision(
        self,
        spec: ProvisionSpec,
        *,
        client: LambdaClient | None = None,
        sleep: Callable[[float], None] | None = None,
        region: str | None = None,
        instance_type: str = _DEFAULT_INSTANCE_TYPE,
        ssh_key_names: list[str] | None = None,
        ready_attempts: int = _DEFAULT_READY_ATTEMPTS,
        tunnel_spawn: secure_tunnel.SpawnFn | None = None,
        tunnel_manager: secure_tunnel.SSHTunnelManager | None = None,
    ) -> ProvisionResult:
        """Launch a Lambda GPU VM for ``spec.model`` and return its serving endpoint once it is active.

        ``client`` defaults to the real httpx client built from ``LAMBDA_API_KEY``; tests inject a fake.
        Guarantees the VM is terminated if anything after launch fails - a failed run leaves nothing
        billable. The vLLM serving comes up via the launch startup script while the VM boots; the first
        chat warms it (the chat UI shows the cold-start state).

        The endpoint is reached over a supervised SSH tunnel (Option A,
        docs/specs/llm-endpoint-secure-transport.md): vLLM binds to loopback on the VM, a fresh
        Anthill-controlled keypair's public half is baked into the launch startup script (no separate
        Lambda API call), and once the VM is active a local port-forward is established and supervised
        by ``tunnel_manager`` (defaults to the process-wide ``secure_tunnel.manager``; tests inject a
        fresh ``SSHTunnelManager()`` for isolation, mirroring ``anthill/remote/tunnel.py``'s test
        convention). ``tunnel_spawn`` is the tunnel subprocess's injectable spawn, for tests - it
        defaults to a real ``ssh`` invocation in production. If the tunnel cannot be established, the
        instance is terminated and this returns ``ok=False`` - there is no fallback to the old public-IP
        endpoint; a failed secure channel must not silently regress to an insecure one.
        """
        if spec.provider != self.key:
            raise ProvisionError(f"spec.provider '{spec.provider}' is not '{self.key}'")
        if client is None:
            client = _RealLambdaClient(os.environ.get("LAMBDA_API_KEY", ""))
        tm = tunnel_manager or secure_tunnel.manager
        do_sleep = sleep or (lambda _s: None)
        region = region or (spec.region or _DEFAULT_REGION)
        ssh_key_names = ssh_key_names or _env_list("LAMBDA_SSH_KEY_NAMES")

        model = (spec.model or "").strip()
        if not model:
            return ProvisionResult(ok=False, status="error", detail="No model selected to serve.")
        # Single-node tensor-parallel (docs/specs/multi-gpu-tensor-parallel-serving.md): derived from the
        # instance type Lambda will actually launch, not a separately-set, separately-validated field -
        # see gpu_count_from_instance_type's docstring for why this also satisfies the "genuine
        # NVLink/NVSwitch node" requirement by construction.
        gpu_count = gpu_count_from_instance_type(instance_type)
        if gpu_count > 1:
            num_attention_heads = _attention_heads_for_model(model)
            if not sizing.gpu_count_divides_heads(num_attention_heads, gpu_count):
                # Refused before anything is launched - no billable VM, nothing to tear down.
                return ProvisionResult(
                    ok=False,
                    status="error",
                    detail=(
                        f"tensor-parallel size {gpu_count} does not evenly divide {model}'s "
                        f"{num_attention_heads} attention heads; choose a gpu_count that divides evenly "
                        "(commonly 2, 4, or 8)."
                    ),
                )
        # The vLLM server sits behind the tunnel, so it MUST still require a key (defense in depth: the
        # tunnel is host-scoped to the Anthill process, not per-caller).
        api_key = secrets.token_urlsafe(32)
        # A fresh Anthill-controlled tunnel keypair per provision (this also gives "rotate on
        # re-provision" for free, per the spec's Security Considerations): its public half is baked
        # into the startup script below so it is present in authorized_keys the moment the VM boots.
        keypair = secure_tunnel.generate_tunnel_keypair()
        authorized_keys_line = secure_tunnel.restricted_authorized_keys_line(
            keypair.public_key_line, remote_port=_VLLM_PORT
        )

        try:
            instance_id = client.launch(
                region=region,
                instance_type=instance_type,
                ssh_key_names=ssh_key_names,
                startup_script=vllm_startup_script(
                    model, api_key, authorized_keys_line=authorized_keys_line, gpu_count=gpu_count
                ),
                name=f"anthill-{model}".replace(":", "-").replace("/", "-").lower()[:60],
            )
        except Exception as e:  # nothing launched yet; nothing to tear down
            return ProvisionResult(ok=False, status="error", detail=f"launch failed: {e}")

        # From here on, a VM exists and is billable: guarantee teardown on any failure.
        tunnel_key = f"lambda:{instance_id}"
        try:
            ip = ""
            for _ in range(max(1, ready_attempts)):
                status, ip = client.instance(instance_id)
                if status == "active" and ip:
                    break
                do_sleep(5)
            if not ip:
                raise ProvisionError("instance did not become active in time")

            # vLLM is loopback-only on the VM; this tunnel is the only reachable path to it. The local
            # port is picked once here and persisted by the caller (provision_run.py) so
            # org_model_endpoint never changes across a later tunnel restart.
            local_port = secure_tunnel.free_local_port()
            tm.start(
                tunnel_key,
                host=ip,
                user=_TUNNEL_USER,
                remote_port=_VLLM_PORT,
                local_port=local_port,
                private_key_openssh=keypair.private_key_openssh,
                spawn=tunnel_spawn,
            )
            if not tm.status(tunnel_key)["running"]:
                raise ProvisionError("SSH tunnel process exited immediately")
        except Exception as e:
            tm.stop(tunnel_key)
            client.terminate(instance_id)  # guaranteed teardown
            return ProvisionResult(
                ok=False, status="error", detail=f"provision failed, torn down: {e}"
            )

        base_url = f"http://127.0.0.1:{local_port}/v1"
        endpoint = OrgEndpoint(
            base_url=base_url,
            api_key=api_key,
            model=model,
            tunnel_private_key_openssh=keypair.private_key_openssh,
            tunnel_local_port=local_port,
            tunnel_remote_host=ip,
        )
        # The endpoint is now loopback-only, reachable solely through the tunnel above -
        # endpoint_is_secure() recognizes a loopback base_url as secure by construction (see
        # hosting/provision.py), so this succeeds under the default configuration with no env flags set,
        # closing the gap docs/specs/llm-endpoint-secure-transport.md describes. This check stays as a
        # defense-in-depth assertion rather than being trusted implicitly.
        if not endpoint_is_secure(endpoint):
            tm.stop(tunnel_key)
            client.terminate(instance_id)
            return ProvisionResult(
                ok=False,
                status="error",
                detail="tunnel endpoint failed the secure-transport check unexpectedly; torn down",
            )
        tp_note = f", tensor-parallel across {gpu_count} GPUs" if gpu_count > 1 else ""
        return ProvisionResult(
            ok=True,
            status="provisioned",
            detail=(
                f"Lambda GPU instance live for {model} at {ip}{tp_note}, reached over a supervised SSH "
                f"tunnel (local port {local_port}); vLLM warming up on first use."
            ),
            endpoint=endpoint,
            handle=instance_id,
        )

    def teardown(
        self,
        handle: str,
        *,
        client: LambdaClient | None = None,
        tunnel_manager: secure_tunnel.SSHTunnelManager | None = None,
    ) -> tuple[bool, str]:
        if not (handle or "").strip():
            return (True, "nothing to tear down")
        (tunnel_manager or secure_tunnel.manager).stop(f"lambda:{handle.strip()}")
        if client is None:
            client = _RealLambdaClient(os.environ.get("LAMBDA_API_KEY", ""))
        client.terminate(handle.strip())
        return (True, f"instance {handle} terminated")


def _env_list(name: str) -> list[str]:
    raw = (os.environ.get(name, "") or "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]
