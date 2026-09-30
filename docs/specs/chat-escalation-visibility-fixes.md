# Spec: three escalation-visibility fixes found in one live-testing round

Status: implemented. Lane: `pillar:model`.
Relates to: `docs/specs/expert-tier-compound-compute-and-escalation.md` (#820's "always offer" design
intent), `docs/specs/agent-mode-web-toggle.md` (found in the same QA pass).

## 1. The always-offer check never ran after agent mode or research

`chat_stream`'s SSE handler answers a turn through one of three branches: `research`, `agent_mode`/
`agent_auto`, or plain chat. The "always offer a one-tap check with your provider" logic (added for
#820, explicitly meant to fire "independent of escalation_mode or whether Automated mode's grader
ran") was written as the last statement inside the plain-chat branch's own indentation - so it was
never reached from the other two branches, including a question the router silently routed into agent
mode (`agent_auto`, #421) without the user ever choosing it.

**Fix:** dedented the block one level, from inside the plain-chat `else:` to a sibling of the whole
`if research / elif agent / else` chain, still inside the same enclosing `try`. Every name it reads
(`cfg`, `escalate_org`, `cache_hit`, `_escalated`, `_pending_escalation_offer`) was already assigned
before the branch chain, so this is a pure indentation fix - no new state, no behavior change to the
plain-chat case itself.

## 2. The "expert" badge landed wherever the answer happened to end

Three live-JS render paths (the automated SSE `escalated` meta event, `escalate-confirm`'s success
callback, `ask-provider-now`'s success callback) built the escalated answer's HTML as
`renderMd(text) + ' <span class="badge-escalated">...'` - concatenated straight onto the end of the
rendered markdown, inside the SAME `.bubble` element. If the answer ended mid-list or right after a
heading, the badge visually attached to that line instead of reading as its own indicator. The
page-reload view (`chat.html`'s Jinja loop) already puts the same badge in a separate sibling
`<div class="meta">` next to `.bubble`, so a fresh answer looked different from one loaded from
history.

**Fix:** a `metaRowFor(bubble)` helper finds-or-creates that sibling `.meta` div (same class the
reload view and the existing `.msg .meta` CSS already use - no new styling needed) and all three call
sites, plus the streamed-answer `_finalize`, now write the badge there instead of into the bubble's
own `innerHTML`.

## 3. A pre-call failure left the client's "Checking with..." indicator stuck forever

The inline Automated-mode escalation yields `{"meta": {"escalating": true}}`, then runs
`record_escalation_used(cfg)`, `db.commit()`, and `audit.log_inference_call(...)` **outside** the
try/except that guarantees an `escalated` or `escalation_failed` follow-up - only the actual provider
call itself was covered. A failure in any of those three statements fell through to the outer bare
`except Exception: pass`, so the client had already been told "escalating" but never learned how it
ended - `chat.html`'s "Checking with {provider}…" indicator has no timeout and only clears on one of
those two signals, so it stayed on screen indefinitely.

**Fix:** widened the try to start right after the `escalating` yield, covering
`record_escalation_used`/`db.commit`/`audit.log_inference_call` as well as the provider call. Every
path that sends `escalating: true` server-side now provably sends exactly one of `escalated: true` or
`escalation_failed: true` afterward, regardless of which statement in that sequence raises.

## 4. Nothing told the user a background escalation had finished

`/chat/{id}/escalate-confirm` and `/chat/{id}/ask-provider-now` both run the actual provider call via
`run_in_threadpool` and persist the answer to a real `ChatMessage` row regardless of whether the
client is still around to see it - but nothing told a user who had moved to a different conversation
that it was done. The app already has a single notification chokepoint (`anthill.web.notify.notify`,
#284) that persists a bell-centre row and best-effort sends a web push - used today for security
alerts and project invites, nothing chat-related.

**Fix:** both endpoints call `notify()` on success, `kind="run"`, linking back to
`/chat/{conv_id}`. Unconditional (not gated on any "is the client still connected" check, since a
plain POST handler has no reliable way to know that) - a redundant bell entry for a user who was
still watching is harmless.

## Verification

`tests/test_chat_automated_escalation.py`: new tests prove the offer now appears after both an
agent-mode and a research-mode answer (previously absent, confirmed red without the fix); a failure
in `record_escalation_used` before the provider call still surfaces `escalation_failed` (previously
silent, confirmed red without the fix); `escalate-confirm` and (`tests/test_ask_provider_now.py`)
`ask-provider-now` both persist a `Notification` row naming the provider and linking to the
conversation. The badge-placement fix has no dedicated Python test (client-side JS, no JS test
runner in this stack) - verified by code review against the exact DOM structure the page-reload
Jinja template already uses. Full suite (2787 passed) and every pre-existing escalation
SSE/offer test pass unmodified.
