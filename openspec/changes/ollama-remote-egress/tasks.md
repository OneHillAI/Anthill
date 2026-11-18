# Tasks

- [x] `stays_local()` decides by address; the test that pinned the old answer is updated; tests for local and
  remote addresses and for the badge through the chat route.
- [x] Move the redaction and restore helpers to `anthill/inference/egress.py`; the OpenAI-compatible backend and
  its tests are unchanged.
- [x] Redact and restore in `OllamaBackend` for a remote address: chat, streaming, tools, with confidence.
- [x] Drop images with a note for a remote Ollama; keep them for a local one.
- [x] Tests for every path, and through the chat route.
- [x] Spec and this change, with the limits (embeddings via `OLLAMA_HOST`; a container name reads as remote; no
  vision through a remote Ollama).
