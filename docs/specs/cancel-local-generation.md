# Spec: stop local generation when the client abandons the stream

Status: implemented
Lane: `pillar:model`
Relates to: `docs/specs/ask-provider-now.md` (#1 UX follow-up to #820/#824), which documented this as a
known, explicitly out-of-scope limitation - closed here on direct founder request.

## 1. Introduction

`docs/specs/ask-provider-now.md` documented that clicking "Ask {Provider} instead" mid-generation
closed the client's `EventSource` but could not stop local generation server-side: an Ollama call
blocks until it returns, and `chat_stream` never checked for early client disconnection. Local kept
running and would still save its own assistant message when it eventually finished, independent of the
provider-answered message the user already saw - wasted local compute, and a real (if usually
short-lived) risk of the same question appearing answered twice if the conversation was reloaded while
local was still finishing.

## 2. Requirements

- THE SYSTEM SHALL stop consuming further local-model output once the client has disconnected, for the
  streaming (`can_stream`) chat path.
- THE SYSTEM SHALL NOT save an assistant message, emit further SSE meta events, or run the Automated-
  mode escalation decision for a turn the client has abandoned - none of it has anywhere to go, and
  saving a partial/abandoned local answer as "this turn's answer" is exactly the duplicate-answer risk
  this closes.
- THE SYSTEM SHALL check for disconnection at existing natural checkpoints (once per streamed token)
  rather than on a timer or a separate polling task - no new latency or resource cost on the normal,
  never-abandoned path.

## 3. Design

- `chat_stream`'s local-streaming loop (`async for token in iterate_in_threadpool(ask_stream(...))`)
  now checks `await request.is_disconnected()` at the top of each iteration, before appending or
  yielding that token. `iterate_in_threadpool` (`starlette.concurrency`) pulls exactly one item at a
  time via `anyio.to_thread.run_sync(next, iterator)` per iteration - a lazy Python generator, so once
  the loop stops asking for the next token, `ask_stream` is simply never advanced again. No explicit
  cancellation of the in-flight Ollama HTTP call is needed: there is nothing left asking for its output.
- On disconnect, a `_client_abandoned_this_turn` flag is set and the loop breaks; immediately after the
  (cache-hit / streaming / non-streaming) branch, that flag short-circuits the rest of the turn with a
  plain `return` from the generator - skipping the `cache_hit`/`slugs`/`auto_web`/`answered_locally`
  meta events, the Automated-mode escalation block, and the "save assistant message" section entirely.
  `_DBSessionMiddleware` closes the request's DB session regardless of how the generator ends, so the
  early `return` needs no explicit cleanup of its own.
- Scoped to the `can_stream` branch only (the common case, and the one "Ask {Provider} instead"
  targets): the cache-hit branch replays an already-computed answer near-instantly, and the
  non-streaming `ask()` fallback (images, web search, cloud policy, or an injection imperative) is a
  single blocking call with no per-token checkpoint to check disconnection at - closing that gap would
  need cancelling the call itself, separate, larger work not attempted here. Agent-mode's own streaming
  loop (`executor.stream`) is a distinct code path and unaffected; "ask the provider instead" doesn't
  apply to agent turns in the first place (see `ask-provider-now.md`'s own scoping).

## 4. Tasks

- [x] Disconnection check added to the `can_stream` loop; `_client_abandoned_this_turn` short-circuits
  the rest of the turn on the very next line after the streaming branch.
- [x] Tests: a simulated mid-stream disconnect stops consuming further tokens (asserted by the
  never-requested tokens being absent, not just the visible output looking short), skips every
  post-loop meta event, and saves no assistant message; a control test confirms the ordinary
  never-disconnects case is unaffected. Verified the disconnect test fails without the fix (all tokens
  stream through regardless) before passing with it.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **The non-streaming `ask()` fallback** (images, web search, cloud policy, injection-imperative
  turns) - a single blocking call with no checkpoint to interrupt; still runs to completion once
  started, same as before this change.
- **Actually cancelling the in-flight Ollama HTTP request** - not attempted or needed: a lazy generator
  simply stops being advanced, which is sufficient to stop this repo's own further work (no message
  saved, no further SSE events), even though the specific in-flight Ollama call already underway for
  the current token (if any) still runs to its own completion in the background.
- **Agent-mode's own streaming loop** - a distinct code path; "ask the provider instead" was already
  scoped away from agent/research turns in `ask-provider-now.md`.
