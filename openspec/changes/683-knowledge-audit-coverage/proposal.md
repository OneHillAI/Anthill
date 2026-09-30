# Phase 6 of #683: fill the real knowledge-mutation audit gaps

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` (requirement 5) asks for an audit-log row on every
knowledge-content mutation, attributed to the acting org and user with the request IP, and claims
"today only `wiki.rejected` and `memory.to_wiki` are logged."

That claim was checked against the real code before implementing anything and found substantially
wrong. `anthill/web/app.py` already calls `audit.log(...)` for `wiki.approved`, `wiki.upload`,
`wiki.upload.confirm`, `wiki.import_connector`, `wiki.principles.saved`, `skill.created`,
`skill.deleted`, `skill.autolearn_toggle`, `skill.proposal_accepted`, `snippet.saved`, `snippet.edit`,
`snippet.to_wiki`, plus several `memory.*` events - a much larger surface than the spec's own
description credited.

The real, narrower gaps, verified line-by-line against the current code:

- `skill_proposal_reject()` logged nothing at all (its sibling, `skill_proposal_accept()`, does).
- `wiki_import_okgf()` (bulk OKGF import) logged nothing at all.
- `approve_review()`/`reject_review()` logged the same `wiki.approved`/`wiki.rejected` event
  regardless of `WikiReview.kind` (`page` | `skill` | `principles`), so a reviewer's decision on a
  skill or on org principles was indistinguishable in the audit log from a decision on an ordinary
  wiki page.
- None of the knowledge-mutation routes had a `request: Request` parameter or used the existing
  `_audit_request()` helper (already used by `/logout` and the data-export routes), so their audit
  rows never captured the request IP.

## What

- `skill_proposal_reject()` now logs `skill.proposal_rejected` (mirroring
  `skill.proposal_accepted`'s shape) when a pending proposal is actually rejected.
- `wiki_import_okgf()` now logs `wiki.import_okgf` after a successful bulk import, including the
  scope and the number of pages queued for review.
- `approve_review()`/`reject_review()` now resolve a kind-aware event name via a new `_review_event()`
  helper: `wiki.approved`/`wiki.rejected` for an ordinary page (unchanged, the default/most common
  case), `skill.approved`/`skill.rejected` for a skill, `principles.approved`/`principles.rejected`
  for org principles.
- The anomaly-detection `RULES` in `anthill/web/audit.py` were checked for any prefix match on the
  renamed events: none exists (`RULES` only prefix-matches `wiki.promote`, a literal event name no
  code path ever emits - a separate, pre-existing dead rule, left alone as out of this PR's scope).
  No update was needed there.
- Every route in this list now takes `request: Request` and audits through `_audit_request(request,
  ...)` instead of a bare `audit.log(db, ...)`, so its audit row captures the client IP: `wiki_upload`,
  `confirm_pdf_upload`, `wiki_import_connector`, `wiki_import_okgf`, `skills_create`, `skills_delete`,
  `skill_proposal_accept`, `skill_proposal_reject`, `snippet_save`, `snippet_to_wiki`, `snippet_edit`,
  `approve_review`, `reject_review`.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 5 (knowledge-change audit and digest) -
this PR ships the audit half only. The digest half (periodic knowledge digest) and requirement 6
(database ledger) are Phase 7 (`683-knowledge-ledger-and-digest`), stacked on top of this branch.
