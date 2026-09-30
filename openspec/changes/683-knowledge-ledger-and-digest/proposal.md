# Phase 7 of #683: knowledge DB registry + periodic digest

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` requirement 6 asks for "a thin database layer over
the files for (a) a page/skill registry, (b) a revision/audit ledger ..., and (c) the data behind the
digest", and requirement 5's second half asks for "a periodic (daily or weekly, configurable) knowledge
digest summarising what changed."

Verified before implementing: no DB registry existed for wiki pages or skills - only pending-review
tables (`WikiReview`/`ProposedSkill`) that cover a change AWAITING approval, not a durable index of
what currently exists. `Workspace.append_log()` (the file-based per-workspace `log.md`) is the existing
ledger and stays exactly as-is - this phase is purely additive over the files, per the spec's own
"files stay the source of truth" framing.

## What

- New `KnowledgeItem` table (`anthill/web/db.py`): `id, org_id, scope, team_id, kind
  ("page"|"skill"|"principles"), slug, title, path, review_state, created_at, updated_at,
  last_editor_id`. A brand-new table, so `create_all` picks it up on existing installs - no migration-
  registry entry needed (same reasoning as Phase 5's `QueuedUpload`).
- New `anthill/web/knowledge_registry.py` (`sync_page()`/`sync_skill()`/`sync_principles()`/`remove()`)
  hooked into as few choke points as possible: `propose_wiki_write()`'s auto-apply branch (the single
  funnel every wiki-page write already goes through), `write_skill()`
  (`anthill/agent/skills.py`), `approve_review()`'s per-kind branches (reusing Phase 6's kind-aware
  event names), and `skills_delete()`. No other route needed to change - traced through the call graph
  to confirm this before committing to the approach.
- A one-time, idempotent startup backfill (`_backfill_knowledge_registry()` in `app.py`, modeled on the
  existing `_migrate_legacy_personal_wiki()` idempotent-application-level-sync style, not a versioned
  schema migration) that scans every existing workspace's pages/skills on disk and registers anything
  not already in `KnowledgeItem`. Only ever inserts a missing row, so it is cheap and safe on every
  boot.
- `AuditLog.registry_id` (nullable, additive `Integer` column - not a `ForeignKey`, matching
  `ProposedSkill.agent_id`'s precedent, since FK enforcement is ON and this is a best-effort
  provenance pointer to a row that can legitimately be deleted later). `audit.log()`/`_audit_request()`
  gained a `registry_id: int | None = None` passthrough, populated only where cheaply available
  (`approve_review()`, which already has the just-synced `KnowledgeItem` row in hand; `write_skill()`
  callers via the returned `Skill.registry_id`) - left `NULL` everywhere else, including deletes.
- `OrgSettings.digest_schedule` (`"off"`/`"daily"`/`"weekly"`, default `"off"`) and
  `digest_last_sent_at` (nullable `DateTime`) - both additive columns, auto-migrated by the existing
  generic `migrate.ensure_columns()` pass (confirmed via a direct before/after test against a
  simulated pre-phase-7 schema), matching the naming/style of the existing `proactivity_mode` /
  `proactivity_last_run` pair.
- New `anthill/web/digest.py::build_digest(db, org_id, since)`: reads `AuditLog` for the corrected
  knowledge-event prefixes from Phase 6 (`wiki.*`, `skill.*`, `principles.*`, `snippet.*`, plus
  `memory.to_wiki`/`memory.promote`/`memory.promote_team` - promotions into or between scopes) and
  returns a `DigestSummary` dataclass (pages changed/rejected, skills learned/adopted/deleted/rejected,
  principles changed/rejected, snippets captured, promotions). No separate "digest data" table - the
  audit log already has everything needed.
- `anthill/web/scheduler.py::_digest_tick()` (new tick in the existing ~60s slow-tick group) +
  `_digest_due()` (reuses the existing `_next_run(schedule, from_dt)` daily/weekly helper rather than
  reimplementing interval math). Notifies each due org's admins via the existing `notify()` chokepoint
  and stamps `digest_last_sent_at` regardless of whether anything changed (an empty digest is silently
  skipped, not sent as noise, but the cadence still advances).
- A small admin-only "Knowledge digest" card on the Audit page (`audit.html`, alongside the existing
  "Anomaly detection rules" card), posting to a new `POST /settings/digest` route.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 5's digest half and requirement 6 (both
marked shipped in this PR).

## Stacking note

This branch (`feat/683-knowledge-ledger-and-digest`) is stacked directly on
`feat/683-knowledge-audit-coverage` (Phase 6, this program's PR immediately before this one) - it
reuses Phase 6's kind-aware audit event names and the `_audit_request()` helper it introduced. The
diff will include Phase 6's commits until that PR merges.
