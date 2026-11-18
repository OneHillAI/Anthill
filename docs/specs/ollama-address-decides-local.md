# Spec: the address decides whether a model call stayed on this machine

Status: implemented. Lane: `pillar:privacy`.

## Problem

`stays_local()` in `anthill/inference/base.py` returned True for every Ollama backend, whatever its address. The
address is a user setting (`ollama_url`), so an Ollama pointed at another machine was still reported as local:
the answer carried the "answered on this device - nothing left it" badge, the audit log and the saved message
recorded it as local, and the escalation and scrubbing gates treated it as on-device. A test pinned the wrong
answer (`stays_local("ollama", "http://anything:11434") is True`).

## Requirements

1. A model call stays local only when its address is on this machine: a loopback address (127.0.0.0/8, `::1`),
   the unspecified address (`0.0.0.0`), `localhost` or a name ending in `.localhost` (case and a trailing dot
   ignored), or `host.docker.internal` and `host.containers.internal` (the host seen from inside a container; the
   repo's `docker-compose.yml` reaches Ollama that way). Any other name or address, including a LAN address and a
   bare name such as `ollama`, is another machine.
2. The address decides, not the backend: an Ollama and an OpenAI-compatible server are judged the same way.
3. An Ollama with no address set is the default one on this machine (blank counts as unset). An unreadable address
   is not local.
4. Only the address's host is read, so `http://localhost.evil.com`, `http://127.0.0.1.evil.com`,
   `http://evil.com/localhost` and `http://localhost@evil.com` are remote.

## Known limits

- This fixes what the product says, not what it does. `OllamaBackend` has no scrubbing path, so text sent to an
  Ollama on another machine is still not scrubbed; it is now reported as having left the machine. Whether to
  scrub it as an OpenAI-compatible endpoint is, is a separate decision.
- A remote Ollama no longer gets the "go deeper" suggestion or the post-run escalation offer, because those apply
  only to an answer given locally.
- A Docker setup that reaches Ollama through a container name (for example `http://ollama:11434`) now reads as
  remote: a bare name cannot be told from another machine, and the safe answer is to say so.

## Acceptance

- `tests/test_stays_local.py` (new): addresses on this machine are local, including the Docker host names and
  an unset Ollama address; a LAN address, a bare name, a name that only starts with `localhost`, and a
  `localhost@evil.com` address are remote, for either backend.
- Through the chat route: an org whose Ollama address is a LAN box gets `"answered_locally": false` in the stream
  and a saved message marked remote; the default address still gets `true`.
- `tests/test_openai_compat.py` no longer pins the old answer.
