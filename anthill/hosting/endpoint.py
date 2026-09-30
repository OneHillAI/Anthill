"""Validate an org model endpoint end to end.

The org plane talks to one standard model endpoint no matter how it was stood up (Ollama, vLLM / TGI, or a
neocloud deploy) - the de-facto ``/v1/chat/completions`` HTTP API those servers all expose (the format is
widely called "OpenAI-compatible", though nothing from OpenAI is used). Before an admin activates the org we
check the endpoint actually works:

    reachable  ->  the chosen model is served there  ->  a real round-trip chat returns something

The two network calls are injectable, so this is unit-testable without any server; the real defaults hit the
standard ``/v1/models`` and ``/v1/chat/completions``. (A separate "org wiki reachable" check is added
when the wiki host is wired; this module covers the model endpoint.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrgEndpoint:
    base_url: str  # the OpenAI-compatible base, including the /v1 suffix
    api_key: str = ""
    model: str = ""  # the model the org will use; "" accepts the server's default
    # Set only by a bare-VM provisioner securing its channel with an SSH tunnel (Option A,
    # docs/specs/llm-endpoint-secure-transport.md) - "" for every other provider/tier. Mirrors how
    # api_key is already optional/empty for a provider that needs none; the caller persists these the
    # same way it already persists api_key, so the tunnel can be re-supervised after an Anthill restart.
    tunnel_private_key_openssh: str = ""
    tunnel_local_port: int = 0
    tunnel_remote_host: str = ""


@dataclass
class Check:
    name: str  # reachable | model_available | round_trip
    ok: bool
    detail: str = ""


@dataclass
class Validation:
    ok: bool
    checks: list[Check] = field(default_factory=list)


def _headers(ep: OrgEndpoint) -> dict[str, str]:
    return {"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {}


def _list_models(ep: OrgEndpoint) -> list[str]:
    import httpx

    r = httpx.get(f"{ep.base_url.rstrip('/')}/models", headers=_headers(ep), timeout=10)
    r.raise_for_status()
    return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]


def list_models(base_url: str, api_key: str = "") -> list[str]:
    """Discover the model ids a self-hosted / VPC endpoint actually serves, so an admin can pick from what
    the box really has rather than a curated shortlist.

    The org endpoint is "OpenAI-compatible" by contract (base includes /v1), which vLLM, TGI, LM Studio and
    Ollama's ``/v1`` all expose - so try ``{base}/models`` first, and ``{base}/v1/models`` when the pasted
    URL omitted the suffix. Fall back to Ollama's native ``/api/tags`` for a box that serves only that.
    Returns a sorted, de-duped list; raises the last error if the server can't be reached at all."""
    import httpx

    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    oai_urls = [f"{base}/models"]
    if not base.endswith("/v1"):
        oai_urls.append(f"{base}/v1/models")

    last_err: Exception | None = None
    for url in oai_urls:
        try:
            r = httpx.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
            if ids:
                return sorted(set(ids))
        except Exception as e:  # try the next shape before giving up
            last_err = e

    # Ollama-native fallback (a box exposing only /api/tags, not the /v1 surface)
    ollama_base = base[:-3].rstrip("/") if base.endswith("/v1") else base
    try:
        r = httpx.get(f"{ollama_base}/api/tags", timeout=10)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
        if names:
            return sorted(set(names))
    except Exception as e:
        last_err = e

    if last_err:
        raise last_err
    return []


def _round_trip(ep: OrgEndpoint, prompt: str) -> str:
    import httpx

    r = httpx.post(
        f"{ep.base_url.rstrip('/')}/chat/completions",
        headers=_headers(ep),
        json={
            "model": ep.model or "default",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8,
            "temperature": 0,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def validate(
    ep: OrgEndpoint,
    *,
    list_models: Callable[[], list[str]] | None = None,
    chat: Callable[[str], str] | None = None,
) -> Validation:
    """Check the endpoint end to end. The two network calls are injectable for tests."""
    lm = list_models or (lambda: _list_models(ep))
    ch = chat or (lambda prompt: _round_trip(ep, prompt))
    checks: list[Check] = []

    # 1. reachable
    try:
        models = lm()
    except Exception as e:
        checks.append(Check("reachable", False, f"can't reach {ep.base_url}: {e}"))
        return Validation(False, checks)
    checks.append(Check("reachable", True, f"{len(models)} model(s) available"))

    # 2. the chosen model is actually served
    if ep.model and ep.model not in models:
        sample = ", ".join(models[:5]) or "none"
        checks.append(
            Check("model_available", False, f"'{ep.model}' is not served (found: {sample})")
        )
        return Validation(False, checks)
    checks.append(Check("model_available", True, ep.model or "(server default)"))

    # 3. a real round-trip chat
    try:
        answer = ch("Reply with the single word: ok")
    except Exception as e:
        checks.append(Check("round_trip", False, f"chat failed: {e}"))
        return Validation(False, checks)
    ok = bool((answer or "").strip())
    checks.append(Check("round_trip", ok, (answer or "").strip()[:80] or "empty response"))
    return Validation(ok, checks)
