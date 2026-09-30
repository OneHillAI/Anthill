# Tasks: PR #661 Tier 2 confidence-based "go deeper" suggestion

## Build steps (completed)

1. Added `looks_uncertain(answer: str) -> bool` to `anthill/agent/intent.py`, mirroring `looks_deep`'s
   conservative, regex-based style - a new `_UNCERTAIN` regex distinct from `_DEEP`.
2. Added `_GO_DEEPER_SUGGESTION` and `_should_suggest_deeper(answer)` to `anthill/wiki/ask.py`, near
   `_NON_ANSWER`/`_NON_ANSWER_MAX_LEN`. `_should_suggest_deeper` is true for `looks_uncertain` OR a short
   `_NON_ANSWER` match (a deliberate decision - see proposal.md).
3. Fixed a real bug found during independent verification of the dev-council's draft: `ask()` never
   assigns `council` on the image-turn path (`if has_img: ... else: council = ...`), so referencing
   `council is None` at the final return would raise `UnboundLocalError` for every image turn. Fixed by
   initializing `council: str | None = None` immediately after `has_img = bool(images_b64)`.
4. `ask()`: cache/publish/file continue to use the plain `answer` unchanged; a new `returned_answer`
   variable (appended with the suggestion when eligible) is what the function actually returns.
5. `ask_stream()`: the single-model streaming branch caches the plain `answer` unchanged, then `yield`s
   the suggestion as one more chunk when eligible. The council-active branch (which delegates entirely to
   `ask()`) needed no separate change - it already benefits from fix #4 by delegation.
6. New tests: `tests/test_intent_depth.py` (added `test_looks_uncertain_precision`, following the file's
   existing list-based style - `UNCERTAIN`/`CONFIDENT` phrase lists); new
   `tests/test_ask_confidence_suggestion.py` (mirrors `tests/test_ask_council.py`'s exact fixture
   conventions - `_FakeBackend`, `_mock_resolved`, `_cfg`/`_noop_decrypt`/`_ws` - plus a new
   `_RecordingCache` double that captures exactly what was passed to `.store()`, since a plain no-op stub
   cannot prove the suggestion was never cached).
7. `docs/specs/chat-depth-autoroute.md` updated in this same PR with a new "Confidence-based suggestion"
   section, and its stale claim that `escalate_org` is "a future multi-model world could use it" corrected
   (confirmed dead end for that purpose - see proposal.md).
8. `ruff check`, `ruff format --check`, `mypy`, full test suite: 1968 passed, 7 skipped, 0 failed.

## A test-writing bug the dev-council's draft introduced, caught and fixed before landing

The draft's own generated test for `looks_uncertain` listed `"I am fully certain this is not documented."`
as an example that SHOULD trigger the detector - actually verified programmatically (ran the exact regex
against this string in isolation before trusting the draft's test): it does NOT match, correctly, since
the sentence expresses high confidence, not hedging. Moved this string to the CONFIDENT/negative list
instead (it is a good negative example: proves the word "certain" alone, without the qualifying "not",
does not false-positive).

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section.
