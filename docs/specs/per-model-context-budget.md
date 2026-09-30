# Spec: Per-model context-token budget

Status: implemented. Lane: `pillar:model`. Issue: #277.

## Problem

Two context budgets were hardcoded regardless of the model in use:

- agent compaction (`agent/executor.py` / `agent/compact.py`): `max_context_tokens = 6000`
- chat-history trimming (`wiki/history.py` via `wiki/ask.py`): `budget_chars = 24000`

A model with a large context window (say 128k) was therefore held to a small fraction of what it can
hold - the running agent transcript and the chat history were summarized/trimmed far earlier than
necessary, losing detail a big-context model could have kept.

## Policy

- `anthill/inference/context.py` derives the budget from the model's context window:
  `token_budget = max(6000, int(window * 0.6))` and `char_budget = max(24000, token_budget * 4)` - a
  fraction of the window (leaving headroom for the response), **floored at the historical values** so a
  small- or unknown-window model never regresses.
- The window comes from the backend, best-effort: `OllamaBackend.context_window(model)` reads the
  `*.context_length` field from Ollama's `/api/show model_info`, cached per (base_url, model) since a
  model's context length is fixed. Returns 0 when it can't be read; `window_for` then uses a safe 8192
  default (so the budget stays at the floor). Backends without a `context_window()` method use the
  default too.
- Wired in: `AgentExecutor` derives `max_context_tokens` from the model window when not given an explicit
  value (an explicit value still wins); `wiki/ask.py` passes a per-model `char_budget` to
  `prepare_history`.

## Acceptance criteria

- With a large-window model, the token/char budgets scale up (e.g. a 128k window -> ~78k-token budget);
  with an unknown or small window they stay at the 6000-token / 24000-char floor.
- `OllamaBackend.context_window` returns the `context_length` from `/api/show`, caches it (one probe per
  model), and returns 0 on any error.
- `AgentExecutor(..., max_context_tokens=N)` still honours an explicit N.
- Because tests run with no reachable Ollama, the probe returns 0 and every budget stays at the floor -
  so no existing behaviour changes. Covered by `tests/test_context_budget.py`.
