# Spec: an Ollama on another machine is remote, and is protected like any remote endpoint

Status: implemented. Lane: `pillar:privacy`. Issue: #245. OpenSpec change: `ollama-remote-egress`.

## Problem

`stays_local()` in `anthill/inference/base.py` returned True for every Ollama backend, whatever its address. The
address is a user setting (`ollama_url`), so an Ollama pointed at another machine was still reported as local:
the answer carried the "answered on this device - nothing left it" badge, the audit log and the saved message
recorded it as local, and the "go deeper" and post-run escalation offers (which apply only to a local answer) were
made. A test pinned the wrong answer (`stays_local("ollama", "http://anything:11434") is True`).

Worse, `OllamaBackend` had no scrubbing path, so everything sent to that Ollama (wiki context, memory, the user's
question) went as written, including anything that looks like an email address, a phone number or an ID, while an
OpenAI-compatible endpoint on the network got its text redacted and its images dropped.

## Requirements

1. **The address decides.** A model call stays local only when its address is on this machine: a loopback address
   (127.0.0.0/8, `::1`), the unspecified address (`0.0.0.0`), `localhost` or a name ending in `.localhost` (case and
   a trailing dot ignored), or `host.docker.internal` and `host.containers.internal` (the host seen from inside a
   container; the repo's `docker-compose.yml` reaches Ollama that way). Any other name or address, including a LAN
   address and a bare name such as `ollama`, is another machine. The backend does not matter: an Ollama and an
   OpenAI-compatible server are judged the same way. An Ollama with no address set is the default one on this
   machine; an unreadable address is not local. Only the host is read, so `localhost.evil.com`,
   `127.0.0.1.evil.com`, `evil.com/localhost` and `localhost@evil.com` are remote.
2. **A remote Ollama is protected.** In every `OllamaBackend` path that sends messages (chat, streaming, tools, with
   confidence) the text is redacted with `anthill.hybrid.scrub` before it is sent, the images are dropped and
   replaced by a short note saying why (nothing can honestly be scrubbed out of pixels), and the reply is restored
   from the placeholders, including a placeholder split across stream chunks, without ever emitting a
   half-restored one. Tool-call arguments are not restored: the model only saw placeholders, and restoring inside
   arbitrary JSON could corrupt a real value. Placeholders are numbered per message so two messages with an email
   each cannot overwrite each other's mapping.
3. **One implementation.** The redaction and restore helpers live in `anthill/inference/egress.py`
   (`scrub_messages`, `restore_stream`) and are used by both `OllamaBackend` and `OpenAICompatBackend`. The
   OpenAI-compatible behaviour and its tests are unchanged (the old private names are still importable from
   `openai_compat`).
4. **A local Ollama is unchanged:** no redaction, images still sent, tokens passed through as they arrive.

## Behaviour users will see

- An Ollama at a LAN box or any other address shows the remote badge, is saved as remote, and gets no "go deeper"
  or post-run escalation offer.
- Vision through a remote Ollama no longer works: the image is not sent, and the answer says to choose a local model
  for images. (Same rule as for any other network endpoint.)
- A Docker setup that reaches Ollama through a container name (for example `http://ollama:11434`) now reads as
  remote and is redacted: a bare name cannot be told from another machine, and the safe answer is to say so. The
  repo's own compose file uses `host.docker.internal`, which stays local.

## Known limits

- **Embeddings are not covered.** `anthill/cache/embedder.py` sends page and question text to the address in the
  `OLLAMA_HOST` environment variable, a separate operator setting from `ollama_url`. Redacting text that is about
  to be embedded would break retrieval, so that path is unchanged. An operator who sets `OLLAMA_HOST` to another
  machine sends that text there unredacted.
- The redaction is the existing `scrub()`: a regex baseline plus Presidio when it is installed. What it cannot
  recognise it cannot remove.
- `anthill/hybrid/**`, `anthill/web/audit.py` and `anthill/wiki/**` are protected paths and are not changed.

## Acceptance

- `tests/test_stays_local.py` (new): addresses on this machine are local, including the Docker host names and an
  unset address; a LAN address, a bare name, look-alike names and `localhost@evil.com` are remote, for either
  backend; through the chat route a LAN Ollama gets `"answered_locally": false` and a saved message marked remote.
- `tests/test_ollama_remote_egress.py` (new): for a remote Ollama the request body holds placeholders and no
  email, and the reply is restored, in chat, with confidence, streaming (split placeholder) and tools (arguments
  untouched); two emails in two messages do not collide; an image is dropped with a note; a local Ollama is sent
  the text as written and keeps its image; through the chat route the request to a LAN Ollama holds no email while
  the user still sees their address in the answer. 11 of its 15 tests fail on the old code.
- `tests/test_openai_compat.py` still passes unchanged apart from the assertion that pinned the old answer.
