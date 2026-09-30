# Tasks

- [x] Re-verify the spec's "only `wiki.rejected` and `memory.to_wiki` are logged" claim against the
      real code before implementing anything. Found it substantially wrong; identified the real,
      narrower gaps instead (see proposal.md).
- [x] `skill_proposal_reject()`: add `audit.log`/`_audit_request` call (`skill.proposal_rejected`).
- [x] `wiki_import_okgf()`: add `_audit_request` call (`wiki.import_okgf`, with a page count).
- [x] `approve_review()`/`reject_review()`: add `_review_event()` helper and split
      `wiki.approved`/`wiki.rejected` into kind-aware event names for skill/principles reviews.
      Checked `anthill/web/audit.py`'s `RULES` anomaly-detection table for any prefix match on the old
      names - none exists, no update needed there.
- [x] Add `request: Request` + switch to `_audit_request()` on every knowledge-mutation route missing
      it: `wiki_upload`, `confirm_pdf_upload`, `wiki_import_connector`, `wiki_import_okgf`,
      `skills_create`, `skills_delete`, `skill_proposal_accept`, `skill_proposal_reject`,
      `snippet_save`, `snippet_to_wiki`, `snippet_edit`, `approve_review`, `reject_review`.
- [x] Tests: `tests/test_knowledge_audit_coverage.py` (9 tests - one clear assertion per gap, plus a
      regression test that ordinary wiki-page approval keeps its existing event name).
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2133 passed,
      7 skipped).
- [x] Update `docs/specs/knowledge-onboarding-and-guidance.md` requirement 5 with an honest shipped
      status: the spec's original framing was wrong, describe the real gaps fixed instead.
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR via the
      app-token bot identity with the `pillar:knowledge` label + `## Disclosure` + `## Spec` sections.
      Monitor check-runs to green; do not merge (human merges in a final batch).

## Bugs found while verifying (not fixed here, out of this PR's scope)

- `anthill/web/audit.py`'s anomaly-detection `RULES` table includes a `("wiki.promote", 5, 10, ...)`
  rule ("Unusual promotion rate"), rendered on the Audit page as "Wiki promotions". No code path ever
  logs an event literally named `wiki.promote` (approvals log `wiki.approved`; the file-based
  per-workspace log uses `ws.append_log("promote", ...)`, a different, non-audit-log mechanism). The
  rule is dead - it can never fire. Pre-existing, unrelated to the event-name split in this PR (it
  didn't match `wiki.approved` before this change either), so left alone rather than expanding scope;
  worth a follow-up.
