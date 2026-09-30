# Chat depth: the system decides, no manual "better answer" levers

## Problem

Chat had manual re-run buttons that asked the user to fetch "a better answer": a "run the multi-step
agent" button and a ☁️ "org model" button ("Not satisfied? Answer again with the bigger org cloud
model"). Per the founder and the frontier benchmark (#421): no user knows when an answer *could* be
better, so a manual "better answer" control is a design failure - users only notice a *bad* answer and
skip it. The system should decide depth; users only explicitly opt into things that are *slow* or *have
side effects* (Deep Research, Agents).

## Design (already shipped, then completed here)

**Router decides depth** (shipped in `24a7665` + `abfe26e`). `intent.classify()` returns a `depth`
("quick" | "deep") from `looks_deep()` (a deterministic multi-hop signal) or the model's read; a clearly
multi-step question auto-escalates to the `AgentExecutor` path (`agent_auto`), which streams its steps.
Natural-language follow-ups replace the re-run buttons: `redo_mode()` maps a short "go deeper" to the
deep agent and "check the web" to a web search. The manual "agent" button is retired.

**This change completes #421 by retiring the last manual "better answer" lever: the ☁️ "org model"
re-run button.** Under one model per account (`docs/specs/one-model-per-account-solo-in-org.md`), that
button is obsolete: the account runs on its one model - in an org account a Solo chat already runs on the
org model (Finding A), and a Solo account has no bigger model to reach. So a per-turn "bump to the bigger
model" no longer means anything: it would only switch the wiki context (personal -> org) or, with no
backend, error. There is nothing to escalate to, so the control is removed rather than replaced with a
natural-language cue.

## Requirements

- The chat answer actions carry no manual "better-answer" re-run button - neither the agent button (already
  retired) nor the ☁️ "org model" button (retired here), in both the server-rendered message meta and the
  live-streamed answer controls.
- Removing it drops the now-dead client code: `redoFromDom`, `redoHeader`, `redoLabel`, the `ORG_AVAILABLE`
  constant, and the `org` parameter of `runStream`.
- Depth is still decided by the router (`classify().depth` + `looks_deep`) and by natural-language
  follow-ups ("go deeper", "check the web"); this change removes only the org-model lever.
- The `escalate_org` request parameter of the chat stream is retained (no UI control drives it), but is
  now confirmed CONFINED to wiki-context scope only (see "Confidence-based suggestion" below for why the
  "future multi-model world" framing this line originally had no longer applies).

## Acceptance criteria

- With an org backend connected and an assistant message present, the chat page contains no
  `redoFromDom(this,'org')` handler, no "org model" re-run button, and no `ORG_AVAILABLE` constant.
- The web and agent re-run buttons remain absent (unchanged).
- `GET /chat/{id}/stream?escalate_org=true` still routes the turn to the org plane (the API capability is
  unchanged), surfacing the not-connected notice when no backend is present.

## Scope

Removal + dead-code cleanup only (`chat.html` + a one-line comment in `app.py`). The depth router itself
is unchanged. Fully automatic model-size routing (picking a bigger model when the local one is too weak)
is a separate concern that depends on fast-default sizing (#413, #416) and is out of scope.

## Confidence-based suggestion (PR #661 Tier 2, added here)

#661's roadmap named a "confidence router... wired into the existing but-unused `escalate_org`
parameter" as a way to reduce reliance on an expensive/cold tier. Re-verified before building anything:
`escalate_org` does not actually reach a different model at all - `anthill/web/plane_routing.py`'s
`plane_inference()` resolves the Solo and Org planes to the SAME model for an org account (only
`wiki_scope`/`use_personal_context` differ), exactly matching what this file already said above ("there
is nothing to escalate to"). Building a confidence router on top of `escalate_org` would wire new logic
into a mechanism that was already confirmed to be a dead end for that purpose.

The council architecture built separately this session already makes `run_council()` run
UNCONDITIONALLY whenever 2+ members are configured - there is no confidence-gating opportunity there; it
always runs. `looks_deep()` above already routes complex-looking QUESTIONS to the deep agent pre-emptively.
What was still missing: nothing caught a low-confidence ANSWER after the fact. `intent.looks_uncertain()`
(a new, separate hedging-language detector, distinct from `looks_deep` and from `wiki.ask._NON_ANSWER`)
now flags a single-model answer (no council used this turn) that hedges, and `wiki.ask.ask()`/
`ask_stream()` append a natural-language suggestion pointing at the EXISTING `redo_mode()` "go deeper"
mechanism - never a silent auto-escalation, matching this file's own core principle (the user opts into
anything slow; going deeper is slower). The suggestion is appended only to what is shown, never to what
is cached, published, or filed as a wiki page. A council-produced answer is never suggested, even if its
text happens to hedge, since the council already cross-checked it.
