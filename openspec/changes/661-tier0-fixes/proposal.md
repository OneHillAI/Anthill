# PR #661 Tier 0: the timeout bug + the Solo-settings inconsistency

## Why

PR #661 ("roadmap for closing the local-vs-frontier capability gap", `docs/specs/
local-vs-frontier-capability-roadmap.md`) names two independent, high-confidence, ship-now findings under
"Tier 0". Both re-verified directly against `origin/main` before writing this (not trusted from the
roadmap doc alone):

1. **A real correctness bug**: `OpenAICompatBackend.__init__` (`anthill/inference/openai_compat.py:15-17`,
   verified verbatim) defaults `timeout: float = 120.0`. A cold start on a large rented model (loading
   weights from cold storage into VRAM) can exceed two minutes - so a cold Org-plane request can
   **hard-fail** with a timeout rather than eventually succeed, independent of any UX/pre-warming work.
2. **Solo settings contradicts a decision the Org plane already made.** Verified: `tests/
   test_hybrid_retired.py` confirms the paid third-party inference-vendor fallback (`anthill/hybrid`) is
   retired from the UI entirely ("no Settings card, no Metrics tile... simply unreachable"), and
   `anthill/hosting/tiers.py:1-9` already has the correct vocabulary (`green` = fully inside your
   perimeter, `amber` = your own account/model but inference transits a third party's shared GPUs). But
   `anthill/web/templates/personalize.html:40-45` still shows "Inference provider" / "per-token" as a
   `coming soon` peer card next to Local/VPC in the Solo settings compute picker - the exact
   already-rejected framing, still implied as a legitimate future direction.

## What already exists (verified, `origin/main`)

`personalize.html`'s compute picker (verbatim, lines 21-46) has FOUR cards: "Local" (live), "Virtual
private cloud" (live), "Self-hosted" (coming soon - Mac mini/box, a DIFFERENT, still-planned feature, NOT
part of this fix), and "Inference provider" / "per-token" (coming soon - the one this fix addresses).
`docs/specs/intelligence-settings.md:18` also lists "Inference provider - per-token (coming soon)" as one
of four Solo compute options - this doc's own roadmap language needs the same correction.

`anthill/hosting/tiers.py`'s docstring (verbatim, lines 1-9):
```python
"""The three org-hosting categories (the "Your cloud" choice).

The org backend runs on infrastructure the organization itself owns. Onehill hosts nothing, and the
org backend never lives on an individual user's personal device. These are NOT the paid per-token vendor
fallback (anthill/hybrid), which leaves the perimeter and is never an org-hosting option.

Sovereignty label:
  green  - fully inside your perimeter (your hardware or your own always-on cloud VPC)
  amber  - your own account and your own model, but inference transits a third party's shared GPUs
"""
```

**Do not confuse this "Inference provider" concept with the product council's "inference-provider" member
lifecycle kind** (`org_council_members[i].lifecycle == "inference-provider"`, a DIFFERENT, newer concept
for referencing a managed OPEN-WEIGHT inference marketplace without Anthill provisioning it - the
council's own Phase 5, which the founder has explicitly decided to skip in this line of work). This Tier 0
fix is about the OLDER, already-retired paid-per-token CLOSED-vendor escalation concept
(`anthill/hybrid`), predating and unrelated to the council's member-lifecycle terminology. Do not let this
fix become an argument for or against Phase 5 - it is not that.

## Acceptance Criteria

1. `OpenAICompatBackend.__init__`'s default `timeout` increases to a value that tolerates a realistic cold
   start on a large rented model (state your chosen number and reasoning explicitly in the PR - the
   roadmap only establishes that 120s is too short, it does not mandate a specific replacement). The
   parameter stays fully overridable, exactly as today - this is a default-value change only, not a
   signature change.
2. `Config.timeout`'s matching default (`anthill/config.py`, currently also `120.0`) is reviewed for the
   same reasoning - change it too if the same cold-start risk applies there, or state explicitly why not
   if it doesn't (e.g. if it's only ever used for a different, always-warm code path).
3. `personalize.html`'s "Inference provider" / "per-token" `coming soon` card is removed from the Solo
   compute picker (matching the org plane's own already-shipped retirement of this exact concept) - the
   "Self-hosted" card (a different, still-planned feature) is untouched.
4. `docs/specs/intelligence-settings.md`'s roadmap language is corrected to match (remove or clearly
   re-scope the "Inference provider - per-token (coming soon)" line) so the spec and the UI stay
   consistent.
5. New/updated tests: a test proving `OpenAICompatBackend`'s default timeout is now the new, higher value
   (a regression guard against silently reverting it); a test proving the "Inference provider" card no
   longer renders in `personalize.html`'s output, while "Self-hosted" still does (proving this fix didn't
   overreach into the other, unrelated coming-soon card).
6. `ruff check`, `ruff format --check`, `mypy`, and the full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers.

## Explicitly out of scope

- Tier 1 (MoE speed-awareness) - already shipped, PR #662.
- Tier 2 (confidence router), Tier 3 (warm pool + fallback UX), Tier 4 (#627/#636) - separate, later
  changes in this same workstream.
- Tier 5 (distributed local pooling) - explicitly deferred, even within #661 itself.
- Anything about the product council's "inference-provider" member lifecycle kind (Phase 5) - unrelated,
  explicitly skipped by the founder in a separate decision.
