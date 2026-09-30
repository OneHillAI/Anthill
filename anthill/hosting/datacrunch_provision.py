"""Live DataCrunch (branded Verda) provisioning - an EU-sovereign Cloud VPC provider.

Mirrors ``lambda_provision.py``: an injectable ``DataCrunchClient`` (so orchestration is fully
unit-tested without the API or a live account), plus a ``_RealDataCrunchClient`` that hits the real
DataCrunch/Verda API over httpx at ``https://api.datacrunch.io/v1``.

Auth is OAuth2 client-credentials (``POST /v1/oauth2/token``). The access token expires
(``expires_in`` seconds), so the real client refreshes it proactively rather than assuming one token
lasts the whole run, with a one-shot retry on a 401 in case it expires mid-call.

NEEDS LIVE VALIDATION against a real DataCrunch account (spends money).

OPEN QUESTION (must be confirmed by a human before this ships): the DataCrunch/Verda create-instance
API (``POST /v1/instances``) does not document a startup-script / user-data / cloud-init field. Lambda's
implementation serves the model by passing a vLLM-on-boot startup script to ``launch()``; DataCrunch
shows no equivalent field, and we refuse to invent one. This provisioner creates the instance and polls
it to ``running``, but the vLLM serving bootstrap is left as a documented gap: the proposed fallback is
an SSH-based post-boot install (matching the on-prem Ollama path's shape) once the instance has an IP.
The success ``detail`` flags this explicitly. All safety properties (guaranteed teardown,
security-by-default) are fully implemented regardless of that open question.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .endpoint import OrgEndpoint
from .provision import DataCrunchPlanner as _DataCrunchPlanner
from .provision import ProvisionError, ProvisionResult, ProvisionSpec, endpoint_is_secure

_BASE = "https://api.datacrunch.io/v1"
_VLLM_PORT = 8000
_DEFAULT_LOCATION = "FIN-01"  # Finland - EU sovereign
_DEFAULT_INSTANCE_TYPE = "1A100.22V"  # a single A100; tune per model on first run
_DEFAULT_IMAGE = "ubuntu-24.04"
_DEFAULT_READY_ATTEMPTS = 60
_TOKEN_SKEW_S = 60  # refresh a bit early to avoid mid-call expiry


@runtime_checkable
class DataCrunchClient(Protocol):
    """The thin DataCrunch/Verda API surface the orchestrator needs. ``_RealDataCrunchClient`` calls
    the API; tests pass a fake."""

    def authenticate(self) -> None:
        """Fetch (or refresh) a bearer token."""
        ...

    def launch(
        self,
        *,
        instance_type: str,
        location_code: str,
        image: str,
        hostname: str,
        ssh_key_ids: list[str],
        description: str,
    ) -> str:
        """Launch a GPU instance; return its id."""
        ...

    def instance(self, instance_id: str) -> tuple[str, str]:
        """(status, public_ip) for an instance; ip is "" until running."""
        ...

    def terminate(self, instance_id: str) -> None:
        """Terminate the instance (idempotent; must not raise if already gone)."""
        ...


class _RealDataCrunchClient:
    """Calls the DataCrunch/Verda API over httpx (OAuth2 client-credentials). NEEDS LIVE VALIDATION
    against a real account (spends money)."""

    def __init__(self, client_id: str, client_secret: str):
        if not (client_id or "").strip() or not (client_secret or "").strip():
            raise ProvisionError(
                "DataCrunch requires DATACRUNCH_CLIENT_ID and DATACRUNCH_CLIENT_SECRET "
                "(one or both are unset)."
            )
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def authenticate(self) -> None:
        import httpx

        r = httpx.post(
            f"{_BASE}/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=30,
        )
        if r.status_code == 401:
            raise ProvisionError("DataCrunch rejected the client credentials (unauthorized).")
        if r.status_code >= 400:
            raise ProvisionError(f"DataCrunch auth error ({r.status_code}): {r.text}")
        data = r.json() or {}
        self._access_token = data.get("access_token")
        self._expires_at = time.time() + float(data.get("expires_in") or 0)
        if not self._access_token:
            raise ProvisionError("DataCrunch auth returned no access_token.")

    def _token(self) -> str:
        if not self._access_token or time.time() >= (self._expires_at - _TOKEN_SKEW_S):
            self.authenticate()
        assert self._access_token is not None
        return self._access_token

    def _req(self, method: str, path: str, payload: dict | None = None) -> dict:
        import httpx

        r = httpx.request(
            method,
            f"{_BASE}{path}",
            headers={"Authorization": f"Bearer {self._token()}"},
            json=payload,
            timeout=30,
        )
        if r.status_code == 401:
            # the token may have expired between the freshness check and the call; refresh once and retry
            self.authenticate()
            r = httpx.request(
                method,
                f"{_BASE}{path}",
                headers={"Authorization": f"Bearer {self._token()}"},
                json=payload,
                timeout=30,
            )
        if r.status_code == 207:
            raise ProvisionError(f"DataCrunch partial failure (207): {r.text}")
        if r.status_code >= 400:
            raise ProvisionError(f"DataCrunch API error ({r.status_code}): {r.text}")
        try:
            body = r.json()
        except Exception:
            body = {}
        return body if isinstance(body, dict) else {"data": body}

    def launch(
        self,
        *,
        instance_type: str,
        location_code: str,
        image: str,
        hostname: str,
        ssh_key_ids: list[str],
        description: str,
    ) -> str:
        body = self._req(
            "POST",
            "/instances",
            {
                "instance_type": instance_type,
                "location_code": location_code,
                "image": image,
                "hostname": hostname,
                "ssh_key_ids": ssh_key_ids,
                "description": description,
            },
        )
        instance_id = body.get("id") or body.get("instance_id") or body.get("data")
        if not instance_id:
            raise ProvisionError(f"DataCrunch launch returned no instance id: {body}")
        return str(instance_id)

    def instance(self, instance_id: str) -> tuple[str, str]:
        body = self._req("GET", f"/instances/{instance_id}")
        data = body if "status" in body else (body.get("data") or {})
        return str(data.get("status") or ""), str(data.get("ip") or "")

    def terminate(self, instance_id: str) -> None:
        try:
            self._req(
                "PUT",
                "/instances",
                {"action": "delete", "id": instance_id, "delete_permanently": True},
            )
        except Exception:
            pass  # idempotent teardown: never raise


class DataCrunchLiveProvisioner(_DataCrunchPlanner):
    """DataCrunch provisioner with a real ``provision``/``teardown`` (the planner ``plan`` is
    inherited)."""

    def available(self) -> tuple[bool, str]:
        return (
            True,
            "DataCrunch / Verda provisioning is wired up (EU-sovereign, Finland). Add your client ID "
            "and secret under Settings -> Organization, then provision; the GPU instance runs until "
            "you tear it down.",
        )

    def provision(
        self,
        spec: ProvisionSpec,
        *,
        client: DataCrunchClient | None = None,
        sleep: Callable[[float], None] | None = None,
        location_code: str | None = None,
        instance_type: str = _DEFAULT_INSTANCE_TYPE,
        image: str = _DEFAULT_IMAGE,
        ssh_key_ids: list[str] | None = None,
        ready_attempts: int = _DEFAULT_READY_ATTEMPTS,
        poll_seconds: float = 5.0,
    ) -> ProvisionResult:
        """Launch a DataCrunch GPU VM for ``spec.model`` and return its serving endpoint once running.

        ``client`` defaults to the real httpx/OAuth2 client built from ``DATACRUNCH_CLIENT_ID`` /
        ``DATACRUNCH_CLIENT_SECRET``; tests inject a fake. Guarantees the VM is terminated if anything
        after launch fails - a failed run leaves nothing billable.
        """
        if spec.provider != self.key:
            raise ProvisionError(f"spec.provider '{spec.provider}' is not '{self.key}'")
        do_sleep = sleep or (lambda _s: None)
        location_code = location_code or (spec.region or _DEFAULT_LOCATION)

        model = (spec.model or "").strip()
        if not model:
            return ProvisionResult(ok=False, status="error", detail="No model selected to serve.")

        try:
            if client is None:
                client = _RealDataCrunchClient(
                    os.environ.get("DATACRUNCH_CLIENT_ID", ""),
                    os.environ.get("DATACRUNCH_CLIENT_SECRET", ""),
                )
                client.authenticate()
        except ProvisionError as e:
            return ProvisionResult(ok=False, status="error", detail=str(e))

        hostname = f"anthill-{model}".replace(":", "-").replace("/", "-").lower()[:60]

        # 1. Launch. If launch fails, nothing was created - nothing to tear down.
        try:
            instance_id = client.launch(
                instance_type=instance_type,
                location_code=location_code,
                image=image,
                hostname=hostname,
                ssh_key_ids=ssh_key_ids or [],
                description=f"anthill vllm serving {model}",
            )
        except Exception as e:  # nothing launched yet; nothing to tear down
            return ProvisionResult(ok=False, status="error", detail=f"launch failed: {e}")

        # From here on, a VM exists and is billable: guarantee teardown on any failure.
        try:
            ip = ""
            for _ in range(max(1, ready_attempts)):
                status, ip = client.instance(instance_id)
                if status == "running" and ip:
                    break
                if status == "offline":
                    raise ProvisionError(f"instance {instance_id} went offline during provisioning")
                do_sleep(poll_seconds)
            if not ip:
                raise ProvisionError("instance did not become active in time")
        except Exception as e:
            client.terminate(instance_id)  # guaranteed teardown
            return ProvisionResult(
                ok=False, status="error", detail=f"provision failed, torn down: {e}"
            )

        api_key = secrets.token_urlsafe(32)
        base_url = f"http://{ip}:{_VLLM_PORT}/v1"
        endpoint = OrgEndpoint(base_url=base_url, api_key=api_key, model=model)
        # SECURITY: same posture as Lambda - see endpoint_is_secure() in provision.py. Refuse and tear
        # down the (billable) instance rather than stand up a cleartext endpoint by default.
        insecure = (
            "plaintext HTTP over a public IP - the API key and traffic are unencrypted; front it with "
            "TLS or use private networking before production "
            "(docs/specs/llm-endpoint-transport-posture.md)"
        )
        if not endpoint_is_secure(endpoint):
            client.terminate(
                instance_id
            )  # do not stand up an insecure endpoint; guaranteed teardown
            return ProvisionResult(
                ok=False,
                status="error",
                detail=(
                    f"refused + torn down: {insecure}. Set ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT=1 to "
                    "accept the cleartext posture and provision anyway."
                ),
            )
        if base_url.startswith("http://"):
            logging.getLogger("anthill.hosting").warning(
                "provisioned an insecure LLM endpoint at %s (ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT is "
                "set): %s",
                ip,
                insecure,
            )
        return ProvisionResult(
            ok=True,
            status="provisioned",
            detail=(
                f"DataCrunch GPU instance running for {model} at {ip}. NOTE: vLLM bootstrap is not yet "
                "automated - DataCrunch's create-instance API has no confirmed user-data field; an "
                "SSH-based post-boot install is pending human confirmation (see module docstring)."
            ),
            endpoint=endpoint,
            handle=instance_id,
        )

    def teardown(self, handle: str, *, client: DataCrunchClient | None = None) -> tuple[bool, str]:
        if not (handle or "").strip():
            return (True, "nothing to tear down")
        if client is None:
            client = _RealDataCrunchClient(
                os.environ.get("DATACRUNCH_CLIENT_ID", ""),
                os.environ.get("DATACRUNCH_CLIENT_SECRET", ""),
            )
        client.terminate(handle.strip())
        return (True, f"instance {handle} terminated")
