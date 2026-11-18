# An Ollama on another machine is remote and protected like any remote endpoint

Full spec: `docs/specs/ollama-address-decides-local.md`. Issue: #245. Lane: `pillar:privacy`.

## Why

`stays_local()` returned True for every Ollama, whatever its address (a user setting). An Ollama on another machine
was labelled "answered on this device", recorded as local, treated as on-device by the escalation gates, and sent
the user's text unredacted, while any other network endpoint got redaction and no images. The Model pillar's first
goal is that a local answer stays local and that anything leaving the machine is visible.

## What changes

- The address decides whether a call stayed on this machine (loopback, `localhost`, and the Docker and Podman host
  names are local; everything else is remote), for either backend.
- A remote Ollama gets the same protection as an OpenAI-compatible endpoint: redacted text, no images, restored
  reply (chat, streaming, tools, with confidence). The helpers move to a shared module.
- Tests for every path and through the chat route. A local Ollama is unchanged.

## Guardrails (do NOT touch)

- No protected path: `anthill/hybrid/**`, `anthill/web/audit.py` and `anthill/wiki/**` are used, not changed.
- No new setting, consent screen or provider flow. Embeddings (`OLLAMA_HOST`) are out of scope and named as a limit.
