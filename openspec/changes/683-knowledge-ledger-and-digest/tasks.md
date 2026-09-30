# Tasks

- [x] Re-verify the grounding before implementing: no DB registry exists today for wiki pages/skills
      (only `WikiReview`/`ProposedSkill`, which cover pending changes, not a live index);
      `Workspace.append_log()` is the current file-based ledger and stays untouched.
- [x] `anthill/web/db.py`: new `KnowledgeItem` table (brand-new table, no migration-registry entry
      needed - `create_all` picks it up).
- [x] New `anthill/web/knowledge_registry.py` (`sync_page`/`sync_skill`/`sync_principles`/`remove`/
      `backfill_workspace`). Hooked into `propose_wiki_write()`'s auto-apply branch, `write_skill()`,
      `approve_review()`'s per-kind branches, and `skills_delete()` - traced the call graph first to
      confirm no other route needs to change for the registry to stay accurate.
- [x] One-time idempotent startup backfill (`_backfill_knowledge_registry()`, wired into the existing
      `@app.on_event("startup")` block alongside `_migrate_legacy_personal_wiki()`), NOT a versioned
      schema migration - only inserts genuinely missing rows, so it is cheap and safe on every boot.
- [x] `anthill/web/audit.py::log()` + `app.py::_audit_request()`: additive `registry_id: int | None`
      parameter. `AuditLog.registry_id`: nullable, additive `Integer` column (NOT a `ForeignKey` -
      verified FK enforcement is ON via the connect-time PRAGMA, and a hard FK would block
      `skills_delete()`'s row removal; matches the existing `ProposedSkill.agent_id` precedent).
      Verified this needs `ensure_columns` handling (an existing table gaining a column) vs.
      `KnowledgeItem` needing none (a brand-new table) by reading `anthill/web/migrate.py`, then
      confirmed both paths work with a direct before/after schema test.
- [x] `anthill/web/db.py::OrgSettings`: `digest_schedule` (`String(10)`, default `"off"`) +
      `digest_last_sent_at` (nullable `DateTime`) - matches the existing `proactivity_mode` /
      `proactivity_last_run` pair's naming/style (found via grep before choosing names).
- [x] New `anthill/web/digest.py::build_digest(db, org_id, since)` - enumerated the real knowledge-
      event prefixes from Phase 6's own changes rather than guessing.
- [x] `anthill/web/scheduler.py`: `_digest_due()` (reuses the existing `_next_run` daily/weekly helper,
      found via grep before writing a new one) + `_digest_tick()` in the existing slow-tick group.
- [x] Admin UI: a "Knowledge digest" card on `audit.html` (checked the existing "Anomaly detection
      rules" card's structure first) + `POST /settings/digest`.
- [x] Tests: `tests/test_knowledge_registry.py` (11 tests - sync/remove/backfill unit behavior, the
      real `propose_wiki_write`/`write_skill`/`approve_review`/`skills_delete` hookups, and a
      registry-linked audit row's `registry_id` resolving correctly) and `tests/test_knowledge_digest.py`
      (14 tests - `build_digest`'s bucketing, `_digest_due`'s cadence gate including naive-datetime
      SQLite round-trip handling, `_digest_tick`'s end-to-end send + stamp + no-double-send, and the
      Audit page's digest card + `POST /settings/digest` route through a real `TestClient`).
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2158 passed,
      7 skipped - the 25 new tests plus every pre-existing test, no regressions).
- [x] Live-verified outside the test suite: triggered `propose_wiki_write` and `write_skill` directly
      against a small seeded DB and confirmed `KnowledgeItem` rows appeared with the right
      kind/scope/slug/title; triggered `build_digest` directly against seeded `AuditLog` rows and
      confirmed sensible bucketed output; drove `GET /audit` and `POST /settings/digest` through a real
      `TestClient` (card hidden with no `OrgSettings` row, shown with the saved cadence selected,
      round-trips a save) and folded that into the test suite above.
- [x] Update `docs/specs/knowledge-onboarding-and-guidance.md` (requirement 5's digest half +
      requirement 6, both marked shipped).
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR (stacked
      on Phase 6, noted explicitly in the PR body) via the app-token bot identity with the
      `pillar:knowledge` label + `## Disclosure` + `## Spec` sections. Monitor check-runs to green; do
      not merge (human merges in a final batch).

## Not independently verified

No real-browser click-through was done for the Audit page's new digest card (no browser tooling
available in this session) - it is instead verified through a real FastAPI `TestClient` driving the
actual routes and rendering the actual Jinja template (not mocked), which catches template errors and
route wiring bugs but not, e.g., CSS/layout issues.
