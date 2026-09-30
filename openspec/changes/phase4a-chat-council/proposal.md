# Phase 4a: wire the council into plain chat answers

## Why

`docs/specs/product-council-architecture.md` R4 commits to wiring the MoA council into three call
sites: chat (`anthill/wiki/ask.py`), scheduled tasks (`anthill/web/scheduler.py`), and agent runs
(`anthill/web/agents_run.py`). Phase 3 (merged, PR #658) built the engine
(`anthill/council/engine.py`: `resolve_council_backends()`, `run_council()`, `CouncilResult`) but wired
it into nothing.

This change (Phase 4a) wires it into the FIRST of those three surfaces only: plain chat. The other two
(scheduler, agent runs) are Phase 4b, a separate change - they are NOT plain text Q&A. They drive a full
ReAct tool-calling loop (`AgentExecutor.run()`) where a single turn can send an email, write a file, or
call an external API. Naively running that whole loop once per council member and combining the text
answers afterward would risk each member independently deciding to fire the same side-effecting tool
call - e.g. three models each sending the same email. That is a real safety problem, not a style
preference, and it needs its own design (Phase 4b: one model executes, others review the finished
result afterward - no duplicate side effects). Do not attempt to solve it here. Phase 4a is scoped
entirely to the surface where "ask N models the same question and synthesize" is actually safe: plain
chat, which never calls tools.

## What already exists (verified against `origin/main` @ 565c1d77, which includes Phase 3)

### The engine (`anthill/council/engine.py`, Phase 3, already merged)

```python
def resolve_council_backends(cfg, decrypt: Callable[[str], str]) -> list[ResolvedMember]: ...

def run_council(
    cfg, question: str, decrypt: Callable[[str], str], *, system: str | None = None,
    temperature: float = 0.2,
) -> CouncilResult: ...
```

`run_council()` today only accepts a flat `(question: str, system: str | None)` pair. Internally it
builds messages via:

```python
def _propose_messages(question: str, system: str | None) -> list[Message]:
    msgs: list[Message] = []
    if system:
        msgs.append(Message(role="system", content=system))
    msgs.append(Message(role="user", content=question))
    return msgs
```

**This is the first thing that must change.** `ask.py`'s existing prompt pipeline
(`anthill/wiki/prompts.py:answer_question()`) already returns a full `list[Message]` - system prompt +
prior conversation turns + the user's question, all baked in:

```python
def answer_question(
    context: str, question: str, history: list[tuple[str, str]] | None = None, *, reassert: bool = False,
) -> list[Message]:
    system = (
        "You are this organization's assistant. Answer the user's question directly and "
        "helpfully. If reference material is provided and relevant, ground the answer in it and cite "
        ...
    )
    ...
```

Forcing `ask.py`'s rich, multi-turn, wiki-grounded prompt through `run_council()`'s current
`(question: str, system: str | None)` shape would mean collapsing history and wiki context into one
string, which is lossy and awkward. **Change `run_council()`'s signature to accept the messages list
directly**: `run_council(cfg, messages: Sequence[Message], decrypt, *, temperature: float = 0.2) ->
CouncilResult`. Drop the separate `system` parameter (a caller who wants a system prompt just includes a
`Message(role="system", ...)` in `messages`, exactly like every other call site in this codebase
already does - see `ask.py`'s `_chat(backend, messages, ...)` and `agents_run.py`'s executor). Update
`_propose_messages` (delete it - callers pass `messages` straight through), `_synthesis_messages` (now
takes `messages: Sequence[Message]` instead of `question: str` + `system: str | None` - derive whatever
display text is needed for the synthesis prompt from the messages list itself, e.g. the last
`role="user"` message), and Phase 3's own tests (`tests/test_council_engine.py`) accordingly. **This is
a pre-release API, used nowhere in production yet (Phase 3 shipped standalone) - there is no
backward-compatibility constraint. Change it outright.**

### `ask()` - the blocking, hardened chat path (`anthill/wiki/ask.py:222-465`, verbatim excerpts)

The single-model "generate locally" step, plain-text branch (line 391, inside a larger function with
cache lookup, org-index lookup, web search, image handling, hijack-retry, and hybrid cloud escalation
around it - ALL OF WHICH MUST STAY EXACTLY AS IS):

```python
            try:
                answer = _chat(backend, messages, model_override, **first_kwargs)
            except BackendError as e:
                if "empty response" not in str(e).lower():
                    raise
                answer = ""
            # Re-run ONCE with the task re-stated AFTER the content (the sandwich) when the first answer
            # looks hijacked OR came back empty ...
            if _looks_hijacked(answer, question, context) or not answer.strip():
                messages = prompts.answer_question(context, question, history=prepared_history, reassert=True)
                try:
                    retry = _chat(backend, messages, model_override, num_predict=ANSWER_MAX_TOKENS, think=False)
                except Exception:
                    retry = ""
                ...
```

`messages` here is exactly the `list[Message]` `run_council()` will now accept. **The integration point
is surgical**: where `ask()` currently does `answer = _chat(backend, messages, model_override,
**first_kwargs)`, it must instead try the council first (when configured) and fall back to the existing
single-backend `_chat(...)` otherwise - see Acceptance Criteria below for the exact fallback rules. The
hijack-retry, empty-retry, image branch, web branch, hybrid escalation, caching, and org-index lookup
are UNTOUCHED - they operate on `answer` (a string) regardless of whether it came from one model or the
council.

`ask()` currently takes `backend: InferenceBackend` and nothing else - it has no `cfg`/`decrypt`
parameters. It needs two new **optional** keyword parameters, `cfg=None` and `decrypt=None`, so every
existing caller that doesn't pass them gets IDENTICAL behavior to today (single backend, no council
possible). Only a caller that explicitly passes both gets council eligibility.

### `ask_stream()` - the fast, true-streaming path (`anthill/wiki/ask.py:468-540`, verbatim)

```python
    # An injection can live in the RETRIEVED context (a poisoned wiki / team / web page), not only the
    # user's question. A streamed answer can't be un-said once the tokens are out, so a suspect turn must
    # NOT stream: hand it to the hardened blocking ask() - which routes to the capable model, checks the
    # output, and re-runs or safely refuses BEFORE anything is shown - and yield its vetted answer as one
    # chunk. Residual-hardening for #541/#568 ... No injection imperative present -> stream normally, unchanged.
    if has_injection_imperative(question) or has_injection_imperative(context):
        answer, _slugs, _hit = ask(ws, question, backend, k=k, history=history, profile=profile,
            principles=principles, memory_context=memory_context, extra_workspaces=extra_workspaces,
            router=router, shared_cache=shared_cache)
        yield answer
        return

    prepared_history = _prepare_history(history, backend, model_override)
    messages = prompts.answer_question(context, question, history=prepared_history)
    chunks: list[str] = []
    for chunk in stream_chat(backend, messages, model_override, num_predict=ANSWER_MAX_TOKENS):
        chunks.append(chunk)
        yield chunk
    ...
```

`ask_stream()` already has exactly the pattern Phase 4a needs for the council case: **delegate the whole
turn to `ask()` and yield its answer as one chunk.** `run_council()`'s answer is only available after
every proposer AND the synthesizer finish - true token-by-token streaming of a council answer is not
possible (there is nothing to stream until synthesis is done). So: widen the existing delegation
condition from "injection-suspect" to "injection-suspect OR council is active for this account", passing
`cfg`/`decrypt` through to the delegated `ask()` call. When the council is not active (0 or 1 resolvable
members), `ask_stream()`'s behavior is completely unchanged - real token-by-token streaming, exactly as
today.

### App route call site (`anthill/web/app.py`, `chat_stream`, verified)

`cfg` and a decrypt callable are already in scope at the exact point `ask()`/`ask_stream()` get called
(the same `cfg`/`_safe_decrypt` already used one screen up for `plane_inference(effective_plane, cfg,
decrypt=_safe_decrypt, prefer_local=use_local)`). Pass them through to `ask()`/`ask_stream()` at both of
this route's call sites (the `can_stream` branch calling `ask_stream()`, and the blocking-fallback branch
calling `ask()`).

### Scope boundary already enforced by existing code, not new here

`ask()`'s image branch, web-search branch, and hybrid-cloud-escalation branch are UNCHANGED - single
backend only, no council, exactly as today. Only the plain, local, text-only "generate the answer"
branch gains council eligibility. This is a deliberate, minimal-blast-radius scope: council-eligible
questions are exactly the questions that were already eligible for true streaming before this change
(no image, no web search, not an org-escalation turn, not injection-suspect) - the same conditions
`app.py`'s existing `can_stream` gate already checks for a different reason (streaming safety). Do not
widen this scope to images or web search in this change.

## Acceptance Criteria

1. `run_council()`'s signature changes to `run_council(cfg, messages: Sequence[Message], decrypt, *,
   temperature: float = 0.2) -> CouncilResult`. `_propose_messages` is removed; each resolved member is
   called with `messages` directly. `_synthesis_messages(messages, proposals) -> list[Message]` replaces
   the old `(question, proposals, system)` signature. Phase 3's tests (`tests/test_council_engine.py`)
   are updated to the new signature - every existing test's INTENT (parallel execution, timeout
   enforcement, partial-failure tolerance, graceful degradation, the executor-shutdown fix) must still be
   proven, just against the new call shape.
2. `ask()` gains optional `cfg=None, decrypt=None` parameters. When both are provided AND
   `resolve_council_backends(cfg, decrypt)` resolves 2+ members, the plain-text "generate locally" step
   uses `run_council(cfg, messages, decrypt)` instead of the single `_chat(backend, messages,
   model_override, **first_kwargs)` call. When `cfg`/`decrypt` are omitted, or fewer than 2 members
   resolve, or every council member fails (`AllMembersFailed`), behavior falls back to today's single
   `_chat(backend, ...)` call - a council failure must never break the chat turn.
3. The image branch, web-search branch, and hybrid-escalation branch of `ask()` are UNCHANGED - always
   single-backend, never council, regardless of `cfg`/`decrypt`.
4. The hijack-retry and empty-retry logic in `ask()` is UNCHANGED and applies equally to a council-
   produced answer (it operates on the resulting string, not on how it was produced). A retry re-invokes
   the SAME resolution logic (council again if it was used the first time).
5. `ask_stream()` gains the same optional `cfg=None, decrypt=None` parameters. Its existing
   injection-suspect delegation-to-`ask()` condition widens to also delegate when
   `resolve_council_backends(cfg, decrypt)` resolves 2+ members (checked once, cheaply, before the
   existing `has_injection_imperative` check - don't resolve backends twice). When delegating for either
   reason, `cfg`/`decrypt` are passed through to the delegated `ask()` call so it can actually use the
   council. When the council is not active, `ask_stream()`'s true streaming path is entirely unchanged.
6. `anthill/web/app.py`'s `chat_stream` route passes `cfg` and the existing `_safe_decrypt` callable
   through to both its `ask()` and `ask_stream()` call sites.
7. New tests with fake/injectable backends (no live account, no network) prove: (a) 0 or 1 resolvable
   council members -> `ask()`/`ask_stream()` behavior is unchanged from before this PR; (b) 2+ resolvable
   members -> the plain-text path uses the council and returns its synthesized answer; (c) all members
   failing degrades to the single-backend fallback, never raising out of `ask()`; (d) the image/web/
   hybrid-escalation branches never invoke the council even when 2+ members are configured; (e)
   `ask_stream()` delegates to `ask()` (one chunk) when the council is active, exactly like it already
   does for an injection-suspect turn.
8. `ruff check`, `ruff format --check`, `mypy`, and the full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers (repo-wide CI slop gates).

## Explicitly out of scope for this change

- Scheduler (`anthill/web/scheduler.py`) and agent runs (`anthill/web/agents_run.py`) - Phase 4b, a
  separate change, because those surfaces execute tools with real side effects and need a different
  design (single model executes, others review afterward - not parallel full-loop execution).
- `verify.py`'s existing cross-check - untouched, per spec R8 ("leave verify.py's sequential cross-check
  unchanged... treat folding verify into the council path as a future follow-up, out of scope here").
  It doesn't run in the plain-chat path today anyway (verified: neither `ask()` nor `ask_stream()` call
  it).
- Image questions and web-search-augmented answers - always single-backend, never council, in this
  change.
- Any new provider/hosting work (the separate "sovereign GPU serving" artifact the user shared, and the
  Mac-mini-always-on-server idea) - unrelated to this change; `resolve_council_backends()` is already
  provider-agnostic and needs no changes for either.
