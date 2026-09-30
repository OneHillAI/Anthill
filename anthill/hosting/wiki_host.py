"""Provision the always-on **wiki / backend host** alongside the org model.

The org backend is the Anthill web app (wiki + review + dashboard). When an admin provisions the model
on a cloud provider with "also bring up the wiki host" on, Anthill stands up a small **always-on CPU
pod** running the backend container, so the whole org backend lives in the org's own cloud instead of on
the admin's laptop. RunPod first (a cheap CPU pod next to the serverless model endpoint); AWS next.

The provider call is behind an injectable ``WikiHostClient`` so the orchestration is fully unit-tested
without a live account. ``_RealRunpodWikiClient`` drives the RunPod pod GraphQL and NEEDS LIVE VALIDATION
against a real account (it spends money) - the pod input fields and the proxy-URL shape are what to
confirm on the first real run. The model serving stays serverless (scale-to-zero); only this small
backend pod is always-on.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol, runtime_checkable

_RUNPOD_GRAPHQL = "https://api.runpod.io/graphql"

# The backend container image the pod runs (the Anthill web app). Built + pushed by
# .github/workflows/backend-image.yml (docker/Dockerfile.backend). PRIVATE on GHCR pre-launch (only this
# org can pull it for testing); flip the package PUBLIC at launch so every org's cloud can pull it. An org
# can override with ANTHILL_BACKEND_IMAGE to pin its own build/mirror.
_DEFAULT_BACKEND_IMAGE = "ghcr.io/onehillai/anthill-backend:latest"

_DEFAULT_DISK_GB = 20
_DEFAULT_READY_ATTEMPTS = 60  # ~5 min at 5s between polls


class WikiHostError(Exception):
    """A wiki-host provisioning step failed (non-fatal to the model: the caller records and moves on)."""


def backend_image() -> str:
    """The container image the wiki/backend pod runs (ANTHILL_BACKEND_IMAGE, else the default)."""
    return (os.environ.get("ANTHILL_BACKEND_IMAGE", "") or _DEFAULT_BACKEND_IMAGE).strip()


@runtime_checkable
class WikiHostClient(Protocol):
    """Stand up + tear down one always-on backend pod. Injected in tests; real impl drives the provider."""

    def launch(self, *, name: str, image: str, disk_gb: int) -> str:
        """Create the always-on CPU pod running ``image``, exposing the backend on HTTP. Returns its id."""
        ...

    def public_url(self, pod_id: str) -> str | None:
        """The pod's public backend URL once it is running, else None (still booting)."""
        ...

    def terminate(self, pod_id: str) -> None:
        """Release the pod. Idempotent; never raises."""
        ...


def provision_wiki_host(
    *,
    name: str,
    client: WikiHostClient,
    image: str = "",
    disk_gb: int = _DEFAULT_DISK_GB,
    ready_attempts: int = _DEFAULT_READY_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
) -> tuple[str, str]:
    """Launch the always-on backend pod and wait for its public URL. Returns (pod_id, url).

    Guarantees teardown of its own pod if it never becomes reachable, so a failed run leaves nothing
    billable. Raises ``WikiHostError`` on failure (the caller keeps the already-provisioned model).
    """
    do_sleep = sleep or (lambda _s: None)
    img = (image or backend_image()).strip()
    try:
        pod_id = client.launch(name=name, image=img, disk_gb=disk_gb)
    except Exception as e:
        raise WikiHostError(f"could not create the backend pod: {e}") from e
    if not pod_id:
        raise WikiHostError("provider did not return a backend pod id")
    # From here a pod exists and is billable: guarantee teardown if it never comes up.
    try:
        for _ in range(max(1, ready_attempts)):
            url = client.public_url(pod_id)
            if url:
                return pod_id, url
            do_sleep(5)
        raise WikiHostError("backend pod did not become reachable in time")
    except Exception:
        client.terminate(pod_id)
        raise


def teardown_wiki_host(pod_id: str, *, client: WikiHostClient) -> None:
    """Tear down the backend pod (best-effort, idempotent)."""
    if not (pod_id or "").strip():
        return
    client.terminate(pod_id)


class _RealRunpodWikiClient:
    """Drives the RunPod **pod** GraphQL over httpx (Bearer auth: the org's cloud RunPod key) to run an
    always-on CPU backend pod. NEEDS LIVE VALIDATION (spends money): confirm the CPU pod input fields and
    that the HTTP proxy URL shape is ``https://{podId}-{port}.proxy.runpod.net`` on the first real run."""

    _PORT = 8000

    def __init__(self, api_key: str):
        if not (api_key or "").strip():
            raise WikiHostError("A RunPod API key is required (set it under Cloud & model).")
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
            raise WikiHostError("RunPod rejected the API key (unauthorized).")
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise WikiHostError(f"RunPod API error: {body['errors'][0].get('message', 'unknown')}")
        return body.get("data") or {}

    def _q(self, s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')

    def launch(self, *, name: str, image: str, disk_gb: int) -> str:
        # gpuCount: 0 => a CPU pod; expose the backend port over RunPod's HTTP proxy.
        data = self._gql(
            f"""mutation {{ podFindAndDeployOnDemand(input: {{
                cloudType: SECURE, gpuCount: 0,
                name: "{self._q(name)}", imageName: "{self._q(image)}",
                containerDiskInGb: {int(disk_gb)}, volumeInGb: {int(disk_gb)},
                volumeMountPath: "/data", ports: "{self._PORT}/http"
            }}) {{ id }} }}"""
        )
        pod_id = (data.get("podFindAndDeployOnDemand") or {}).get("id")
        if not pod_id:
            raise WikiHostError("RunPod did not return a backend pod id.")
        return pod_id

    def public_url(self, pod_id: str) -> str | None:
        data = self._gql(
            f'query {{ pod(input: {{podId: "{self._q(pod_id)}"}}) {{ runtime {{ uptimeInSeconds }} }} }}'
        )
        runtime = (data.get("pod") or {}).get("runtime") or {}
        if runtime.get("uptimeInSeconds") is None:
            return None  # not running yet
        return f"https://{pod_id}-{self._PORT}.proxy.runpod.net"

    def terminate(self, pod_id: str) -> None:
        try:
            self._gql(f'mutation {{ podTerminate(input: {{podId: "{self._q(pod_id)}"}}) }}')
        except Exception:
            pass  # idempotent teardown: never raise
