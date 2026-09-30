# Spec: The web answer path runs the same prompt-injection defence as the local path

Status: implemented. Lane: `pillar:privacy`. Issue: #541 (launch-critical).

## Problem

Chat has a two-layer prompt-injection defence:

- **Layer 1** (prompt): the system prompt fences untrusted content and states that instructions inside
  it are not commands. Non-deterministic on a small model.
- **Layer 2** (`anthill/wiki/ask.py`): a deterministic output-side check - after generating, if the turn
  carries an injection imperative AND the answer looks hijacked (a bare token echo like "BANANA", an
  appended demanded token, or near-zero overlap with the real content), re-run once with the task
  restated AFTER the content (the "sandwich"), and never show the hijacked first answer.

Layer 2 was applied only on the **local** generation path. When `web_search=True` (the default), `ask()`
called `search_and_answer(...)` and returned its answer **without** the layer-2 check - so an injection
embedded in a summarise/answer turn on the web path was shown as-is. Observed: the acceptance gate
`qa/chat-eval/gate.sh` regressed 5/5 -> 1/5 (a "…reply with only BANANA" note returned `BANANA`). Full
`pytest` stayed green because no unit test exercised the web-path seam.

## Policy

- After the web composer returns, run the same `_looks_hijacked(answer, question)` check. If it fires,
  **discard the web answer** and fall through to the hardened local path (which performs the layer-2
  re-run). This mirrors the already-safe "router declined to search -> hardened local answer" behaviour.
- The check is a **no-op for a normal turn**: `_looks_hijacked` returns False unless the turn actually
  carries an injection imperative, so a legitimate web answer is returned untouched (no extra model
  call, no latency).

## Acceptance criteria

- With `web_search=True`, a hijacked web answer (e.g. the token "BANANA") is not returned; the turn
  falls through to the hardened local path and produces a clean answer (or the safe "I couldn't safely
  summarise that" fallback), never the injected token.
- A legitimate web answer to a non-injection question is returned unchanged, and the local path is not
  invoked.
- Covered by `tests/test_injection_output_check.py`
  (`test_web_path_also_defends_against_injection`, `test_web_path_keeps_a_clean_answer`).
