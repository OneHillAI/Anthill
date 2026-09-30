"""Live RunPod provisioning - the first concrete Direction-A provider.

RunPod is the standard neocloud for now: friction-free (no GPU quota approval), and one account can host
the per-use model (serverless), training, and an always-on wiki pod. This implements ``provision`` /
``teardown`` for a RunPod **serverless vLLM endpoint** behind an injectable ``RunpodClient`` so the
orchestration - create, poll-to-registered, and *guaranteed teardown on failure* - is fully unit-tested
without any network or live account.

``_RealRunpodClient`` is the only part that touches RunPod (and real money). It calls RunPod's **GraphQL
API directly over httpx** (already a dependency) rather than the ``runpod`` SDK - the SDK drags in ~20 heavy
transitive deps (paramiko, sentry, a CLI...) and downgrades cryptography, which we will not bundle into the
app. The two create mutations (``saveTemplate`` / ``saveEndpoint``) mirror the SDK's exactly; the
``deleteEndpoint`` mutation and the GPU pool are the bits to confirm on first live run. The provider-agnostic
``Provisioner`` interface is unchanged, so Lambda / OVH / Scaleway / AWS / GCP slot in the same way.

Invariants honored (mirroring training/backends/base.py): a cost cap (max workers + scale-to-zero idle), and
**guaranteed teardown** - a failed provision never leaves a billable endpoint behind.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .endpoint import OrgEndpoint, Validation
from .provision import ProvisionError, ProvisionResult, ProvisionSpec
from .provision import RunpodProvisioner as _RunpodPlanner

_RUNPOD_GRAPHQL = "https://api.runpod.io/graphql"
# RunPod's vLLM worker publishes NO `latest`/`stable` tag on the registry - only pinned versions
# (`v2.22.0`, ...), so `runpod/worker-v1-vllm:stable` 404s with "image was not found on the registry".
# Pin a known-good release; override with RUNPOD_VLLM_IMAGE if you need a newer one. Bump periodically.
_DEFAULT_RUNPOD_VLLM_IMAGE = "runpod/worker-v1-vllm:v2.22.0"
_DEFAULT_GPU_IDS = "AMPERE_24"  # 24 GB Ampere pool; tune per model size on first live run
_DEFAULT_DISK_GB = 25

# Cost guardrails: a serverless endpoint scales to zero when idle, and is capped at a small worker
# count so a runaway never balloons. Tunable per deployment later.
_DEFAULT_MAX_WORKERS = 1
_DEFAULT_IDLE_SECONDS = 30
_DEFAULT_READY_ATTEMPTS = 60  # times to poll for the endpoint to come up


@dataclass(frozen=True)
class RunpodDeploy:
    """What a created RunPod serverless endpoint looks like to the orchestrator."""

    endpoint_id: str
    base_url: str  # the OpenAI-compatible base, e.g. https://api.runpod.ai/v2/<id>/openai/v1


@runtime_checkable
class RunpodClient(Protocol):
    """The thin RunPod surface the orchestrator needs. ``_RealRunpodClient`` wraps the SDK; tests
    pass a fake. Keeping this tiny is what makes the provisioner testable and provider-swappable."""

    def create_serverless_endpoint(
        self,
        *,
        name: str,
        hf_model: str,
        max_workers: int,
        idle_seconds: int,
        gpu_ids: str = _DEFAULT_GPU_IDS,
        hf_token: str = "",
        min_workers: int = 0,
    ) -> RunpodDeploy:
        """Create a serverless vLLM endpoint serving ``hf_model``; return its id + OpenAI base URL."""
        ...

    def endpoint_ready(self, endpoint_id: str) -> bool:
        """True once a worker is live and can serve (vs still cold/building)."""
        ...

    def delete_endpoint(self, endpoint_id: str) -> None:
        """Tear the endpoint down (idempotent; must not raise if already gone)."""
        ...


def _q(s: str) -> str:
    """Escape a string for inline GraphQL (RunPod's API takes inline args, not variables)."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def _vllm_image() -> str:
    """The vLLM worker image to deploy. Read at call time so it can be overridden without a rebuild."""
    return (os.environ.get("RUNPOD_VLLM_IMAGE") or "").strip() or _DEFAULT_RUNPOD_VLLM_IMAGE


class _RealRunpodClient:
    """Calls RunPod's GraphQL API over httpx. NEEDS LIVE VALIDATION against a real account (spends
    money). The ``saveTemplate``/``saveEndpoint`` mutations mirror the runpod SDK's exactly; the
    delete mutation + GPU pool are the bits to confirm on the first live run.
    """

    def __init__(self, api_key: str):
        if not (api_key or "").strip():
            raise ProvisionError("A RunPod API key is required to provision (none configured).")
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
            raise ProvisionError("RunPod rejected the API key (unauthorized).")
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise ProvisionError(f"RunPod API error: {body['errors'][0].get('message', 'unknown')}")
        return body.get("data") or {}

    def create_serverless_endpoint(
        self,
        *,
        name: str,
        hf_model: str,
        max_workers: int,
        idle_seconds: int,
        gpu_ids: str = _DEFAULT_GPU_IDS,
        hf_token: str = "",
        min_workers: int = 0,
    ) -> RunpodDeploy:
        # 1) a serverless template for the vLLM worker, with the model as an env var. A HF token (for
        # gated repos like Llama) is added only when provided, under both the names vLLM/HF honor.
        env = [f'{{ key: "MODEL_NAME", value: "{_q(hf_model)}" }}']
        if (hf_token or "").strip():
            tok = _q(hf_token.strip())
            env.append(f'{{ key: "HF_TOKEN", value: "{tok}" }}')
            env.append(f'{{ key: "HUGGING_FACE_HUB_TOKEN", value: "{tok}" }}')
        tpl = self._gql(
            f"""mutation {{ saveTemplate(input: {{
                name: "{_q(name)}-tpl", imageName: "{_vllm_image()}", dockerArgs: "",
                containerDiskInGb: {_DEFAULT_DISK_GB}, volumeInGb: 0, ports: "",
                env: [{", ".join(env)}],
                isServerless: true, startSsh: false, isPublic: false, readme: ""
            }}) {{ id }} }}"""
        )
        template_id = (tpl.get("saveTemplate") or {}).get("id")
        if not template_id:
            raise ProvisionError("RunPod did not return a template id.")
        # 2) the serverless endpoint. workersMin 0 (the default) scales fully to zero when idle;
        # a nonzero min_workers keeps that many workers warm to bound cold starts, at ongoing cost
        # (PR #661 Tier 3 - this used to be hardcoded to 0, so the admin-facing "optional warm pool"
        # planner step text was aspirational, not real).
        ep = self._gql(
            f"""mutation {{ saveEndpoint(input: {{
                name: "{_q(name)}", templateId: "{template_id}", gpuIds: "{_q(gpu_ids or _DEFAULT_GPU_IDS)}",
                networkVolumeId: "", locations: "",
                idleTimeout: {int(idle_seconds)}, scalerType: "QUEUE_DELAY", scalerValue: 4,
                workersMin: {int(min_workers)}, workersMax: {int(max_workers)}
            }}) {{ id }} }}"""
        )
        endpoint_id = (ep.get("saveEndpoint") or {}).get("id")
        if not endpoint_id:
            raise ProvisionError("RunPod did not return an endpoint id.")
        return RunpodDeploy(
            endpoint_id=endpoint_id,
            base_url=f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1",
        )

    def endpoint_ready(self, endpoint_id: str) -> bool:
        # A scale-to-zero serverless endpoint has 0 workers until the first request; "ready" here just
        # means it is registered (the first chat warms it - the chat UI shows the cold-start state).
        data = self._gql("query { myself { endpoints { id } } }")
        endpoints = ((data.get("myself") or {}).get("endpoints")) or []
        return any(e.get("id") == endpoint_id for e in endpoints)

    def delete_endpoint(self, endpoint_id: str) -> None:
        # A scale-to-zero endpoint bills nothing while idle; delete is best-effort and never raises.
        try:
            self._gql(f'mutation {{ deleteEndpoint(id: "{_q(endpoint_id)}") }}')
        except Exception:
            pass


class RunpodLiveProvisioner(_RunpodPlanner):
    """RunPod provisioner with a real ``provision``/``teardown`` (the planner ``plan`` is inherited)."""

    _needs = "Provide a RunPod API key for your own account."

    def available(self) -> tuple[bool, str]:
        # The implementation is wired up; it just needs the org's RunPod API key at call time.
        return (
            True,
            "RunPod provisioning is wired up. Add your RunPod API key under Settings -> "
            "Organization, then provision; the endpoint scales to zero when idle.",
        )

    def provision(
        self,
        spec: ProvisionSpec,
        *,
        client: RunpodClient | None = None,
        validate: Callable[[OrgEndpoint], Validation] | None = None,
        sleep: Callable[[float], None] | None = None,
        max_workers: int = _DEFAULT_MAX_WORKERS,
        idle_seconds: int = _DEFAULT_IDLE_SECONDS,
        ready_attempts: int = _DEFAULT_READY_ATTEMPTS,
        gpu_ids: str = _DEFAULT_GPU_IDS,
        hf_token: str = "",
        name_suffix: str = "",
        min_workers: int = 0,
    ) -> ProvisionResult:
        """Create a RunPod serverless endpoint for ``spec.model`` and confirm it is registered.

        ``client`` defaults to the real httpx client built from ``RUNPOD_API_KEY``; tests inject a fake.
        A serverless endpoint scales to zero, so we do NOT force a synchronous round-trip on provision
        (a cold model load can take minutes); registration is success, and the first chat warms it (the
        chat UI shows the cold-start state). Pass ``validate`` to additionally check serving (tests do).
        Guarantees teardown if anything after creation fails - a failed run leaves nothing billable.

        The deployment name carries a short unique ``name_suffix`` (a random hex by default; injectable
        for tests) because RunPod requires template names to be unique - a deterministic name would
        collide with the template a previous (e.g. failed) attempt left behind ("Template name must be
        unique") and block every retry.
        """
        if spec.provider != self.key:
            raise ProvisionError(f"spec.provider '{spec.provider}' is not '{self.key}'")
        if client is None:
            client = _RealRunpodClient(os.environ.get("RUNPOD_API_KEY", ""))
        do_sleep = sleep or (lambda _s: None)

        model = (spec.model or "").strip()
        if not model:
            return ProvisionResult(ok=False, status="error", detail="No model selected to serve.")

        suffix = (name_suffix or uuid.uuid4().hex[:8]).strip()
        base = f"anthill-{model}".replace(":", "-").replace("/", "-").lower()[:48]
        name = f"{base}-{suffix}"  # unique per attempt -> never collides with a leftover template

        try:
            deploy = client.create_serverless_endpoint(
                name=name,
                hf_model=model,
                max_workers=max_workers,
                idle_seconds=idle_seconds,
                gpu_ids=gpu_ids or _DEFAULT_GPU_IDS,
                hf_token=hf_token,
                min_workers=min_workers,
            )
        except Exception as e:
            return ProvisionResult(ok=False, status="error", detail=f"create failed: {e}")

        # From here on, an endpoint exists and is billable: guarantee teardown on any failure.
        try:
            ready = False
            for _ in range(max(1, ready_attempts)):
                if client.endpoint_ready(deploy.endpoint_id):
                    ready = True
                    break
                do_sleep(5)
            if not ready:
                raise ProvisionError("endpoint did not register in time")

            if (
                validate is not None
            ):  # optional extra serving check (tests; not the serverless default)
                result = validate(OrgEndpoint(base_url=deploy.base_url, api_key="", model=model))
                if not result.ok:
                    detail = "; ".join(f"{c.name}: {c.detail}" for c in result.checks)
                    raise ProvisionError(f"validation failed: {detail}")
        except Exception as e:
            client.delete_endpoint(deploy.endpoint_id)  # guaranteed teardown
            return ProvisionResult(
                ok=False, status="error", detail=f"provision failed, torn down: {e}"
            )

        return ProvisionResult(
            ok=True,
            status="provisioned",
            detail=f"RunPod serverless endpoint live for {model} (scales to zero when idle).",
            endpoint=OrgEndpoint(base_url=deploy.base_url, api_key="", model=model),
            handle=deploy.endpoint_id,
        )

    def teardown(self, handle: str, *, client: RunpodClient | None = None) -> tuple[bool, str]:
        if not (handle or "").strip():
            return (True, "nothing to tear down")
        if client is None:
            client = _RealRunpodClient(os.environ.get("RUNPOD_API_KEY", ""))
        client.delete_endpoint(handle.strip())
        return (True, f"endpoint {handle} deleted")
