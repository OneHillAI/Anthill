# Spec: flagged wiki writes must durably commit

Status: implemented
Lane: `pillar:knowledge`
Relates to: `#827` (Flagged wiki uploads are silently lost - WikiReview row never committed).

## 1. Introduction

QA's alpha smoke test found that a flagged `/wiki/upload` returns `?saved=queued` (a success message)
while the `WikiReview` row it should have created never actually persists: `/wiki/review/personal`
stays empty, the page never applies, and the uploaded content is gone with no error anywhere. Root
cause, confirmed in code: `propose_wiki_write()` (`anthill/web/app.py`) does `db.add(WikiReview(...));
return False` on the flagged path with no commit. `_DBSessionMiddleware` only **closes** a request's
DB sessions at the end of the request - it was never a commit mechanism - so an uncommitted add is
silently discarded (rolled back) the moment the response finishes.

The bug is systemic, not specific to `/wiki/upload`: `propose_wiki_write` is called from eight sites
across `app.py` and `scheduler.py`. Four of them (`research_topic`, both snippet-to-wiki routes,
memory-to-wiki) already call `db.commit()` themselves afterward and are unaffected. `wiki_upload` and
`confirm_pdf_upload` call it via the shared `_ingest_and_propose` helper, which returns without
committing - both were affected. `scheduler.py`'s two callers happen to commit later in the same
function for an unrelated reason, masking the same underlying gap there too.

The bug was invisible to the existing test suite for two independent reasons: `test_wiki_personal_
review.py`'s tests construct a `WikiReview` directly with an already-committed session (never
exercising `propose_wiki_write`'s own commit responsibility), and `test_document_upload.py`'s fake
model backend always returns `flags: []`, so no existing upload test ever took the flagged branch at
all.

## 2. Requirements

- THE SYSTEM SHALL durably persist a flagged wiki write's `WikiReview` row before the HTTP response
  that reports it (`?saved=queued`) is sent - a request that later reads it back in a fresh session
  (the same way a real subsequent page load does) SHALL find it.
- THE SYSTEM SHALL fix this at the single shared gate (`propose_wiki_write`), not by patching
  individual call sites - it is documented as "the single gate for a wiki write" and is called from
  eight places; committing inside it is correct for all of them (an already-committing caller's
  later `db.commit()` becomes a harmless no-op on an empty transaction) and prevents the same class of
  bug for any future caller that assumes something else commits on its behalf.
- A regression test SHALL cross a real request/session boundary - constructing a `WikiReview` directly
  in an already-committed test session, or reading it back via the same session a route used, does not
  exercise the bug this spec fixes.

## 3. Design

- One line: `db.commit()` in `propose_wiki_write`, immediately after `db.add(WikiReview(...))` and
  before `return False`.
- `tests/test_document_upload.py`: a new test forces a real, mechanical flag (a dangling `[[link]]`)
  through the actual `/wiki/upload` route for a personal-scope upload - personal scope skips the
  model-judgement review pass entirely (issue #428), so this needs no fake LLM review JSON, only a
  fake ingest-summary reply containing the dangling link (the summariser's reply becomes the page body
  verbatim for a single-chunk upload). Reads the result back via a **fresh** `_SessionFactory()`
  session, then closes the loop with a second, independent request to `/wiki/review/{id}/approve`,
  proving a later request can find and act on what an earlier one created.
- Verified the test actually catches the bug: reverting the one-line fix while keeping the test
  reproduces the exact reported symptom (`NoResultFound` reading the review back).

## 4. Tasks

- [x] `db.commit()` added to `propose_wiki_write`'s flagged branch.
- [x] Regression test crossing the real request/session boundary, plus the approve-it-back-durably
  follow-through QA asked for.
- [x] Verified: full suite green (2699 passed), the new test fails without the fix and passes with it.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **The over-eager mechanical flag QA separately noted** (a plain one-line personal note getting
  flagged) - a distinct tuning question about the review gate's sensitivity, not a data-loss bug; left
  for a separate follow-up.
- **The clean (auto-applied) path's best-effort registry sync** - that path writes the page to disk
  first (the real source of truth) and only best-effort mirrors it into the DB registry; losing that
  mirror is a known, accepted, non-user-visible gap, unlike the flagged path where the DB row IS the
  only record of the proposal.
