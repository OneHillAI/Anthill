"""Plane-aware execution routing for the chat/task surfaces (two-plane architecture, P2).

Turns a conversation's plane + the org config into the concrete inference settings and the
context scope the executor should use:

  solo -> the local Ollama model + the personal wiki + personal memory (offline-capable).
  org  -> the organization's shared model endpoint + the org wiki, with personal context
          EXCLUDED (the privacy invariant: personal context is never sent to the org/cloud model).

org routing requires a reachable backend: if the org plane is selected without a validated endpoint,
``plane_inference`` raises ``PlaneUnavailable`` so the caller can surface it - it never silently
falls back to the local model.

This module is the testable seam; the chat-stream route applies the returned settings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .. import planes
from .db import normalize_topology

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5:3b"


class PlaneUnavailable(RuntimeError):
    """The org plane was requested but its backend is not connected/validated."""


@dataclass(frozen=True)
class PlaneInference:
    """Resolved inference + context settings for one conversation/task, by plane."""

    plane: str  # solo | org
    backend: str  # "ollama" | "openai"
    base_url: str
    model: str
    api_key: str | None
    wiki_scope: str  # "personal" | "org" - the primary wiki to ground answers in
    use_personal_context: bool  # include personal wiki + personal memory? solo True, org False
    ephemeral: bool = False  # P4 personal-mode-on-org: serve on the org model but retain nothing


def _org_api_key(cfg, decrypt: Callable[[str], str]) -> str | None:
    """The API key for the org inference endpoint, or None when none is configured.

    RunPod serverless authenticates its inference endpoint with the SAME API key used to provision,
    and the provisioning flow never copies it into org_model_key. So for a RunPod org with no explicit
    model key, fall back to the provision key - otherwise EVERY call (chat, the reachability probe that
    gates the Org button, and validation) hits the endpoint with no token and the RunPod gateway
    returns 401 "no token provided".
    """
    key_enc = getattr(cfg, "org_model_key_enc", "") or ""
    if not key_enc and (getattr(cfg, "org_provider", "") or "").strip().lower() == "runpod":
        key_enc = getattr(cfg, "org_provision_key_enc", "") or ""
    if not key_enc:
        return None
    try:
        return decrypt(key_enc)
    except Exception:
        return None


def _org_endpoint(cfg, decrypt: Callable[[str], str]) -> tuple[str, str, str | None] | None:
    """(endpoint_url, model, api_key) for a validated org backend, else None (not connected)."""
    endpoint = (getattr(cfg, "org_model_endpoint", "") or "").strip()
    if not planes.org_available(cfg) or not endpoint:
        return None
    return endpoint, (getattr(cfg, "org_model", "") or ""), _org_api_key(cfg, decrypt)


def org_endpoint_connected(cfg, decrypt: Callable[[str], str]) -> bool:
    """Cheap, non-network check: is there a validated org/RunPod/inference-provider endpoint this
    account could escalate a turn to? For callers (Chat's redo suggestion, an Agent's escalation gate)
    that only need to know whether the option exists, not a live reachability probe
    (``org_reachability`` does that, at real network cost)."""
    return _org_endpoint(cfg, decrypt) is not None


def shares_org_model(cfg) -> bool:
    """True only for a genuine multi-user ORG install (``deployment_topology == "org"``) with a shared
    backend configured - narrower than ``planes.is_org_mode``, which is true for ANY account that ever
    validated an endpoint, solo-topology included. A solo-topology account that connects its own
    RunPod/inference-provider endpoint but keeps ``solo_compute == "local"`` as its default is not
    "an org" in the sharing sense this flag gates - it's one person choosing to keep a stronger backend
    available on demand (#278's escalation engine), not a team sharing one model."""
    return (
        planes.is_org_mode(cfg)
        and normalize_topology(getattr(cfg, "deployment_topology", "") or "") == "org"
    )


def _local_serve(cfg) -> tuple[str, str] | None:
    """(base_url, model) when a locally-trained fine-tune is being served via the on-device mlx-lm
    OpenAI endpoint, else None. Set by the trainer on promotion and on boot; cleared when the server
    is down. When present, the Solo/local plane serves the fine-tune instead of the bare Ollama model
    - still fully on-device, nothing leaves."""
    url = (getattr(cfg, "local_serve_url", "") or "").strip()
    if not url:
        return None
    model = (getattr(cfg, "local_serve_model", "") or "").strip()
    return url, model


def plane_inference(
    plane: str | None,
    cfg,
    *,
    decrypt: Callable[[str], str],
    prefer_local: bool = False,
) -> PlaneInference:
    """Resolve how a conversation/task in ``plane`` should run, given the org config (``cfg``).

    Org routing needs a validated endpoint; raises ``PlaneUnavailable`` otherwise (never falls back
    to local). ONE MODEL PER ACCOUNT: in an org account a Solo conversation runs on the ORG model too
    (the tier decides SHARING, not the model) - kept private (personal wiki, ephemeral) - falling back
    to the local model only when the org endpoint is unreachable.

    ``prefer_local`` is the user's "use my local model now" choice for a Solo-cloud (VPC) account whose
    endpoint is unreachable: it forces this Solo run onto the on-device model + the (always-local) personal
    wiki, instead of the VPC. The offline fallback is a PROMPTED choice, never a silent downgrade - so this
    only applies when the user asked for it. It is ignored for Org/Team runs (those have no local option;
    offline they wait). ``decrypt`` turns the stored encrypted org key into the plaintext used for the call.
    """
    if planes.normalize(plane) == "org":
        org = _org_endpoint(cfg, decrypt)
        if org is None:
            raise PlaneUnavailable(
                "The organization model backend is not connected. Connect and validate it under "
                "Settings -> Organization, or start a Solo chat."
            )
        endpoint, model, api_key = org
        return PlaneInference(
            plane="org",
            backend="openai",
            base_url=endpoint,
            model=model,
            api_key=api_key,
            wiki_scope="org",
            use_personal_context=False,
        )

    # Team: grounds in the team wiki. In an org install it runs on the org cloud model (needs the
    # backend, like Org; personal context excluded); in Solo it is a local project on the local model.
    if planes.normalize(plane) == "team":
        if planes.is_org_mode(cfg):
            org = _org_endpoint(cfg, decrypt)
            if org is None:
                raise PlaneUnavailable(
                    "The organization model backend is not connected. Connect and validate it under "
                    "Settings -> Organization, or use a Solo chat."
                )
            endpoint, model, api_key = org
            return PlaneInference(
                plane="team",
                backend="openai",
                base_url=endpoint,
                model=model,
                api_key=api_key,
                wiki_scope="team",
                use_personal_context=False,
            )
        served = _local_serve(cfg)
        if served is not None:  # a promoted on-device fine-tune serves the local planes
            url, model = served
            return PlaneInference(
                plane="team",
                backend="openai",
                base_url=url,
                model=model,
                api_key=None,
                wiki_scope="team",
                use_personal_context=True,
            )
        return PlaneInference(
            plane="team",
            backend="ollama",
            base_url=(getattr(cfg, "ollama_url", "") or _DEFAULT_OLLAMA_URL),
            model=(getattr(cfg, "ollama_model", "") or _DEFAULT_MODEL),
            api_key=None,
            wiki_scope="team",
            use_personal_context=True,
        )

    # Solo on the user's OWN cloud compute: when solo_compute == "cloud", a Solo run executes on the
    # configured cloud endpoint (the same backend the org uses) instead of the on-device model, so a
    # single user can run a big frontier-class open model. It is your own cloud, so the run is NOT
    # ephemeral; personal context + personal wiki are kept. Falls back to local (below) if no endpoint
    # is connected - it is an enhancement, never a hard requirement. Takes precedence over the ephemeral
    # personal-mode borrow (your own cloud beats borrowing a shared org model).
    # ``prefer_local`` (the user's offline "use local now" choice) skips the VPC and drops to the
    # on-device model below - the local wiki is unchanged, so it is a seamless, weaker-model fallback.
    if not prefer_local and str(getattr(cfg, "solo_compute", "") or "local").lower() == "cloud":
        own = _org_endpoint(cfg, decrypt)
        if own is not None:
            endpoint, model, api_key = own
            return PlaneInference(
                plane="solo",
                backend="openai",
                base_url=endpoint,
                model=model,
                api_key=api_key,
                wiki_scope="personal",
                use_personal_context=True,
            )

    # ONE MODEL PER ACCOUNT (Finding A): in a genuine multi-user ORG account (``deployment_topology ==
    # "org"``, a shared org backend configured), a Solo chat runs on the ORG model too - the tier
    # decides SHARING, not the model. It stays PRIVATE: the personal wiki (not the org wiki) grounds it,
    # and the run is ephemeral - nothing is retained org-side, trained into the org model, or visible to
    # anyone else. Falls through to the local model below when the org endpoint is unreachable (the
    # local model is the offline fallback); ``prefer_local`` skips it. The solo-cloud (own VPC) case
    # returned above, so reaching here with a backend and org topology means a genuine ORG account.
    #
    # Narrowed from ``planes.is_org_mode`` to ``shares_org_model`` (#278): a solo-topology account that
    # connects its own endpoint but keeps ``solo_compute == "local"`` is one person choosing to keep a
    # stronger backend available on demand, not a team sharing one model - it must stay on local by
    # default so the escalation engine (redo "use the connected backend") has an actual local answer to
    # judge and escalate FROM, instead of every turn already silently running on the connected endpoint.
    if not prefer_local and shares_org_model(cfg):
        org = _org_endpoint(cfg, decrypt)
        if org is not None:
            endpoint, model, api_key = org
            return PlaneInference(
                plane="solo",
                backend="openai",
                base_url=endpoint,
                model=model,
                api_key=api_key,
                wiki_scope="personal",
                use_personal_context=True,
                ephemeral=True,
            )

    # Plain Solo: the local model + personal context. If a locally-trained fine-tune is being served
    # on-device (mlx-lm OpenAI endpoint), use it; otherwise the bare Ollama model.
    served = _local_serve(cfg)
    if served is not None:
        url, model = served
        return PlaneInference(
            plane="solo",
            backend="openai",
            base_url=url,
            model=model,
            api_key=None,
            wiki_scope="personal",
            use_personal_context=True,
        )
    return PlaneInference(
        plane="solo",
        backend="ollama",
        base_url=(getattr(cfg, "ollama_url", "") or _DEFAULT_OLLAMA_URL),
        model=(getattr(cfg, "ollama_model", "") or _DEFAULT_MODEL),
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
    )


def org_reachability(cfg, *, decrypt: Callable[[str], str], list_models=None) -> dict:
    """A lightweight reachability probe for the org backend, for the chat offline state.

    Returns a small JSON-able dict ``{available, state, detail}`` where ``state`` is one of:
      ready        - the endpoint answered (the Org plane is usable)
      unreachable  - a backend is configured but did not respond (offline, down, or a cold start)
      unavailable  - no validated org backend is connected

    ``list_models`` is injectable for tests; the default does a real ``/models`` GET. A non-ready
    result never raises - the chat UI reads this to gate Org actions, and it must not crash.
    """
    from ..hosting import endpoint as ep_mod

    endpoint_url = (getattr(cfg, "org_model_endpoint", "") or "").strip()
    if not planes.org_available(cfg) or not endpoint_url:
        return {
            "available": False,
            "state": "unavailable",
            "detail": "No org backend is connected.",
        }
    # Use the same key resolution as chat/inference: a provisioned RunPod backend has an empty
    # org_model_key and authenticates with the provision key. Without this the probe 401s and the
    # chat UI grays out the (otherwise usable) Org button half a second after it loads.
    api_key = _org_api_key(cfg, decrypt) or ""
    ep = ep_mod.OrgEndpoint(
        base_url=endpoint_url, api_key=api_key, model=(getattr(cfg, "org_model", "") or "")
    )
    lm = list_models or (lambda: ep_mod._list_models(ep))
    try:
        models = lm()
    except Exception:
        return {
            "available": True,
            "state": "unreachable",
            "detail": "The org backend is not responding.",
        }
    return {"available": True, "state": "ready", "detail": f"{len(models)} model(s) available"}
