# PR #661 Tier 2: suggest going deeper when a single-model answer looks uncertain

## Why (re-scoped from #661's original description - re-verified before writing this)

#661's roadmap doc describes Tier 2 as "a confidence router... wired into the existing but-unused
`escalate_org` parameter." Re-verified directly against `origin/main` before building anything: this
premise does not hold, independent of anything else changed this session.

`anthill/web/plane_routing.py:106-118`'s `plane_inference()` shows that for an org-mode account, BOTH the
"solo" and "org" planes resolve to the exact SAME model/endpoint - the only difference is `wiki_scope`
(personal vs org) and `use_personal_context`. So `escalate_org=True` in `chat_stream` never actually
reaches a bigger or different model - it only changes which wiki context is used. This matches what
`docs/specs/chat-depth-autoroute.md` already says about this exact parameter: "there is nothing to
escalate to" under one-model-per-account. Building a "confidence router" on top of it would wire new logic
into a dead end.

Separately, the product council work shipped earlier this session (Phases 3/4a/4b) already makes
`run_council()` run UNCONDITIONALLY whenever 2+ members are configured - no confidence gating needed or
possible there; it always runs. `anthill/agent/intent.py`'s `looks_deep()` already routes clearly
multi-hop-looking QUESTIONS to the deep agent, pre-emptively, based on the question's phrasing.

**What is actually still missing**: nothing catches a low-confidence ANSWER after the fact. A single-model
turn (no council configured, or a council that fell back to one member) that comes back hedging
("it's possible that...", "I'm not entirely sure, but...") is shown to the user exactly like any other
answer - full confidence implied, none actually earned. This is the real, previously-unaddressed gap Tier
2 should close, following the existing philosophy this codebase already established in
`chat-depth-autoroute.md`: "the system should decide depth; users only explicitly opt into things that are
slow or have side effects." A confidence signal on the ANSWER should offer a natural-language nudge to go
deeper (reusing the EXISTING `redo_mode()`/deep-agent mechanism), not silently auto-escalate - going deeper
is slower, so per that same established philosophy it stays the user's call, just an easier one to make.

## What already exists and is REUSED

- `anthill/agent/intent.py:392-414`'s `looks_deep(message) -> bool` and its `_DEEP` regex - the exact style
  to mirror for a new, distinct `looks_uncertain(answer) -> bool` (a hedging-language detector on the
  ANSWER, not the question - a different signal, not a duplicate).
- `anthill/agent/intent.py:436-449`'s `redo_mode(message) -> str` ("deep"|"web"|"") and the deep-agent path
  it triggers - the existing mechanism a low-confidence answer should nudge the user toward, not a new
  escalation pipeline.
- `anthill/wiki/ask.py:81-91`'s `_NON_ANSWER` regex - a DIFFERENT, narrower, already-existing concept
  (outright refusal/non-answer, e.g. "I don't have that information", used only for cache-worthiness).
  `looks_uncertain` must be distinct from this - hedging within a substantive answer, not a flat refusal.
- `anthill/wiki/ask.py`'s `ask()` and `ask_stream()` - the two answer-producing functions extended.

## Explicit design decision: suggestion, not silent auto-escalation

Per `chat-depth-autoroute.md`'s own established principle (the system decides depth automatically; the
user opts into anything slow), a low-confidence answer gets a natural-language SUGGESTION appended (e.g.
"This answer isn't fully certain - say 'go deeper' for a closer look"), reusing the existing
`redo_mode()`/deep-agent mechanism the user already knows how to invoke. It does NOT silently re-run the
deep agent automatically.

## Explicit design decision: a refusal also gets the suggestion

`_NON_ANSWER` (outright refusal) and `looks_uncertain` (hedging within a substantive answer) are kept as
two DISTINCT detectors, but a short `_NON_ANSWER`-matching refusal ALSO receives the "go deeper" suggestion
- a refusal is arguably the MOST uncertain outcome, and going deeper (the multi-step agent, or a web
search) is exactly the useful next step there too. Stated explicitly here rather than left to accident.

## A real bug found during independent verification (fixed before landing)

`ask()` has an `if has_img: ... else: council = None if suspect else _council_answer(...)` structure -
`council` is ONLY assigned in the non-image `else` branch. Referencing `council is None` at the function's
final return (to gate the suggestion) would have raised `UnboundLocalError` for every image-analysis turn.
Fixed by initializing `council: str | None = None` immediately after `has_img = bool(images_b64)`, before
either branch - semantically correct too, since an image turn never uses the council at all.

## Explicitly out of scope

- `TrainingExample.task_type` (unused column, #661 also names this) - the roadmap's own text frames this as
  "a follow-on" (per-task-type LoRA adapters), a separate, larger fine-tuning-specialization initiative.
- Any change to `escalate_org`, `plane_inference`, or the org/solo plane-routing logic - confirmed dead end
  for this purpose; not reused, not removed (it may still serve its actual purpose of picking wiki-context
  scope, untouched by this proposal).
- Auto-escalating to a bigger/different model when one happens to be configured - the suggestion always
  points at the SAME existing `redo_mode "deep"` mechanism, not a model swap.
- Any UI beyond the natural-language suggestion text itself (no new button, no new SSE event type).

## Acceptance criteria

1. `looks_uncertain(answer: str) -> bool` exists in `anthill/agent/intent.py`, mirrors `looks_deep`'s
   style, and is tested against both real hedging phrasing and confident phrasing that must NOT trigger it.
2. The two answer-producing paths (`ask()`'s single-model branch, `ask_stream()`'s single-model streaming
   branch) both append the suggestion when `_should_suggest_deeper(answer)` is True AND no council was
   used for this turn.
3. The suggestion is appended only to what is returned/shown - never to what is cached, published, or
   filed as a wiki page (tested by capturing exactly what was passed to those calls, not just the return
   value).
4. New/updated tests cover: `looks_uncertain` true/false cases; the suggestion appears for a single-model
   uncertain answer in both `ask()` and `ask_stream()`; the suggestion does NOT appear when a council
   answered (even if the synthesized text happens to contain hedging language); a council configured but
   fully failed (falls back to single) remains eligible; an image turn remains eligible (regression guard
   for the UnboundLocalError bug above).
5. `ruff check`, `ruff format --check`, `mypy`, full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers.
