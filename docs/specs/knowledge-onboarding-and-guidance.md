# Knowledge onboarding and guidance

Status: in progress. Lane: `pillar:knowledge`. Split into 7 independently-shippable phases (a
companion fix-batch of 4 correctness bugs shipped first, then requirements 1-6 below in phase order).
Shipped so far: the companion fix-batch, phase 1 (requirement 1, interactive walkthroughs), phase 2
(requirement 2 + the "Concept simplification" section - the snippet-to-wiki capture bridge and "turn a
chat into a skill"), and phase 3 (requirement 3 - guided skill authoring: conformance, governance
scopes, local validation, a real "does it trigger?" check, a vendored template gallery, and
auto-distillation surfaced to non-admins).

Relates to:
- `docs/specs/declutter-onboarding-and-org-settings.md` (implemented) - shipped the in-chrome setup
  steps and the solo/non-admin setup CTA. This spec builds on that; it does not re-do general onboarding.
- `docs/specs/model-onboarding-and-sovereignty.md` (proposed) - the larger compute-onboarding redesign.
  This spec is the KNOWLEDGE half (wiki/memory/snippets/skills), a sibling, not a substitute.
- `docs/specs/audit-log-coverage.md` (implemented) - audits session end and data EGRESS. This spec adds
  the complementary half: auditing knowledge-content MUTATION. No overlap.
- The local vision-model licence swap is tracked separately (urgent standalone), not here.

## Problem

Anthill has four knowledge surfaces - Wiki, Memory, Snippets, Skills - and a review found all four are
built and sound. The release gap is not capability, it is guidance: a normal user does not understand
what each surface is, why it helps, or what to do. Concretely, from the review:

- The wiki never explains itself in-product, and its central idea (the model writes pages, you approve,
  you do not hand-edit) is a surprise. What explanation exists is a wall of text.
- Skills is the worst case: a cloud user typically lands on a completely empty Skills page, the
  governance/scope fields are hidden, and the auto-distillation that could help only fires from
  scheduled agent runs (never from chat), so most users never see the feature work at all.
- Snippets are a separate silo that, surprisingly, is never retrieved back into answers, so a user who
  "collects" one expecting reuse is let down. The value (gold signal, can become a wiki page) is only
  explained on the list page, not at capture time.
- Getting documents in is inconsistent: uploads are converted and summarised, connector imports
  (Drive/Notion) are filed as raw text, and Office files cannot be ingested at all.
- Knowledge changes are almost entirely un-audited, and there is no periodic account of what changed.

The founder's framing: everyone knows the wiki is great and skills are helpful, but nobody understands
how, why, or what to do. This spec closes that UX gap. The through-line is guidance, delivered as short
interactive walkthroughs (not text walls) and one consistent capture pattern across surfaces.

## Concept simplification: snippets are self-added wiki elements

> **Status: shipped**, via the bridge option this section's own escape hatch allows, not a table
> merge - re-verified against the real code before building:
>
> - `anthill/web/snippets.py::save_snippet()` truly never touched the wiki, and `grep -rn "Snippet"
>   anthill/wiki/` truly returns nothing - both confirmed as claimed.
> - The one existing path into the wiki, `POST /snippets/{id}/wiki` -> `snippet_to_wiki()`
>   (`anthill/web/app.py`), defaults `target_scope` to `"org"` through a review-gated click the user
>   had to discover on the `/snippets` list, exactly as claimed.
> - One planning-pass claim did NOT hold up: there is no `_can_review()` function, and personal-scope
>   proposals do not auto-apply because "the proposer is reviewing their own item." The real mechanism
>   is `outline_change()` in `anthill/wiki/review.py`: for `scope == "personal"` it skips the entire
>   model review pass (contradiction/duplicate/quality/PII checks) and applies mechanical checks only
>   (dangling `[[links]]`, a near-duplicate title) - "a user's private notes apply immediately." The
>   actually-named `_can_approve()` only decides who may later approve an item that DID get queued
>   (the proposer themselves, for personal scope) - it plays no role in the common auto-apply case.
>   The PR body of the implementing change documents this correction; code comments cite the real
>   functions.
> - Implementation choice: bridged, not merged. `snippet_save()` now also calls the existing
>   `propose_wiki_write(..., target_scope="personal", ...)`, so a captured snippet becomes a real
>   `.md` page in the user's personal `Workspace` (the same file `anthill/wiki/ask.py` retrieves from)
>   the moment it is saved, and `Snippet.wiki_slug` is stamped immediately so a later promotion
>   (`snippet_to_wiki`) edits that SAME page instead of forking a second one. The `Snippet` table
>   itself is untouched and still exists (still the audit trail + `TrainingExample`/corroboration
>   source) - only its outcome changed, per this section's own "the requirement is the behaviour, not
>   the table" escape hatch.
> - Gap being honest about: this is not a 100%-guaranteed instant write. The existing mechanical
>   checks can still queue a personal-scope page (e.g. a snippet containing `[[a link to a page that
>   doesn't exist]]`), same as any other personal wiki write always could. The snippet itself is never
>   lost either way (`tests/test_snippet_wiki_bridge.py::test_flagged_personal_write_is_queued_not_lost`).

A snippet is repositioned from a separate store into the manual way a user adds to their own wiki. This
collapses four concepts toward three for the user: Wiki (your knowledge, however it is added), Memory
(automatic, recalled live), Skills (reusable procedures). It also fixes the "collected but never reused"
gap, because wiki content is retrieved at answer time.

- THE SYSTEM SHALL make a captured snippet a first-class, retrievable element of the user's personal
  wiki scope, so it is grounded into future answers like any other wiki content (today snippets are not
  on the live-retrieval path).
- The existing capture UX and value (the one-line "red line" rationale, the gold `TrainingExample`, the
  `content_key` corroboration path, and provenance back to the source turn) SHALL be preserved.
- Promotion from personal to team/org SHALL continue through the existing wiki review gate.
- Implementation MAY either bridge the `Snippet` store into wiki retrieval or merge it into the wiki
  page store; the requirement is the behaviour (retrievable, self-added wiki content), not the table.

## Requirements

### 1. Self-explanation as interactive walkthroughs

> **Status: shipped.** Per-surface dismissible walkthroughs added for Wiki, Memory, Snippets, and
> Skills (`anthill/web/static/walkthrough.js`, `User.completed_walkthroughs`), independent of each
> other and of the pre-existing global onboarding tour. The prose explainers themselves were kept
> (not replaced) - they still hold useful reference detail; the walkthrough is additive guidance on
> top, not a replacement copy-edit.

- THE SYSTEM SHALL replace the current prose explainers for Wiki, Memory, Snippets, and Skills with a
  short, clickable, step-through walkthrough per surface (for example: what a wiki is -> how a page is
  made -> you approve, you do not hand-edit -> done), dismissible and re-openable from the surface.
- Each walkthrough SHALL be skippable and SHALL record completion so it is not forced again, without
  suppressing the other surfaces' walkthroughs (the current tour can be ended early and never resumed).

### 2. One capture pattern across surfaces

> **Status: shipped.** A captured snippet now also writes an immediate personal-wiki page
> (`snippet_save()` in `anthill/web/app.py` calls the existing `propose_wiki_write()` at
> `target_scope="personal"`), and a "Turn this conversation into a skill" action
> (`POST /skills/draft-from-chat`) sits next to the snippet capture button in `chat.html`, drafting a
> skill via the existing `draft_skill()` and handing off to the guided wizard on `/skills` to review
> and save. See the "Concept simplification" section above for what was verified about the wiki side
> of this before building it (the `_can_review()` auto-apply rule this spec's own planning pass
> assumed exists turned out not to; the real mechanism is different and is documented there).

The snippet capture gesture (a per-message button, a floating "save" button on text selection, and a
light tag modal) is the proven pattern. It SHALL be applied consistently:

- "Save to wiki" (snippet capture, per the reframe above) - retained and clarified.
- THE SYSTEM SHALL add a "Turn this conversation into a skill" capture action using the same gesture,
  which drafts a skill from the conversation and opens the guided wizard (section 3) to refine it.
- Each capture action SHALL be discoverable: a persistent, unobtrusive note on the relevant surface
  (for example on the Skills page) SHALL tell the user this can be done from chat, so the affordance is
  not hidden.
- At capture time THE SYSTEM SHALL state the payoff briefly (what happens to this now, and how it can
  be shared later), rather than only on the list page.

### 3. Skills: guided and semi-automated

> **Status: shipped**, with several claims re-verified and corrected against the real code first:
>
> - **agentskills.io conformance** (when-to-use folded into `description`): `skill_md()`
>   (`anthill/agent/skills.py`) now emits `description: <description> Use when: <when_to_use>` when
>   both are given, while still ALSO emitting `x-anthill-when-to-use` unchanged (Anthill's own cheap
>   keyword matcher and the authoring UI read that field directly - the fold-in is additive, not a
>   replacement). `tests/test_agentskills_conformance.py` and `docs/AGENT_SKILLS.md`'s worked example
>   updated to match.
> - **Governance fields settable in the UI**: confirmed first that enforcement was already real
>   (`anthill/agent/executor.py`: a skill only activates for an identity holding every one of its
>   declared scopes) - the actual gap was that `skills.html`'s create/edit form and `skills_create()`
>   never exposed or accepted a `scopes` field, so every hand-authored skill silently shipped
>   `scopes=[]` (vacuously unrestricted). Added a scopes checkbox group (the same `_ALL_SCOPES`
>   vocabulary as `AgentIdentity`), threaded through `skills_create()` into `write_skill()`/`skill_md()`,
>   and exposed via `/skills/{slug}/raw` for the edit-prefill.
> - **Local validation**: new `anthill/agent/skills.py::validate_skill()` + `POST /skills/validate`,
>   called client-side on every save attempt and again server-side inside `skills_create()`. Checks
>   naming (reuses `validate_skill_name()`), a description nudge, a rough WORD-count budget for
>   instructions (not a real tokenizer count - honestly labelled as a heuristic in the warning text
>   itself), and dangling `[[asset]]` references. Only a naming error blocks save - an empty
>   description stays a warning, since `skill_md()` already falls back to a generic one and existing
>   skills created without a description (see `tests/test_skills_scoped.py`) must keep working.
> - **"Does it trigger?" check**: the spec's own wording ("against the local model") doesn't match how
>   skill matching actually works - `anthill/agent/executor.py`'s real run-time selection is
>   `match_skills()`'s cheap keyword overlap, not a model call. `POST /skills/check-trigger` reuses
>   that exact function against a one-off draft, so the check can never drift from real behaviour - a
>   separate, model-judged heuristic would have been LESS faithful, not more, so this deliberately
>   does not build one.
> - **Template gallery**: real internet access was available and used to verify licensing directly
>   against `github.com/anthropics/skills` rather than assuming - `anthill/skills_gallery/` vendors 4
>   entries (`brand-guidelines`, `frontend-design`, `internal-comms`, `theme-factory`), each with its
>   own upstream `LICENSE.txt` confirmed as Apache License 2.0 (the repo has no single repo-wide
>   license; docx/pdf/pptx/xlsx are explicitly source-available and excluded, as is `skill-creator`
>   itself per this section's own "without embedding that tool" requirement). See
>   `anthill/skills_gallery/SOURCES.md` for the full audit trail, exactly what was and wasn't vendored,
>   and the modifications made. `load_gallery()`/`GET /skills/gallery`/`POST
>   /skills/gallery/{slug}/adopt` keep it separate from `ANTHILL_SKILLS_DIR` (inert until adopted) and
>   route adoption through the identical `write_skill()`/review-gate path a hand-made skill uses.
> - **Auto-distillation surfaced to non-admins**: the "Learn skills from agent runs" banner
>   (`skills.html`) was admin-gated in its entirety; the informational text now renders for everyone,
>   only the Pause/Resume control stays admin-gated (that route already requires an admin). Distilling
>   from a chat conversation (not only scheduled runs) shipped in phase 2 as
>   `POST /skills/draft-from-chat` (requirement 2, "one capture pattern"), not duplicated here.
> - Found and fixed one real, latent bug while building this: `parse_skill_md()` displayed a
>   genuinely-conformant third-party skill's raw `name` (a lowercase-hyphenated slug, e.g.
>   "brand-guidelines") verbatim as its title, instead of prettifying it like the loose-file fallback
>   already did - invisible until this PR vendored the first real external skills this codebase has
>   ever loaded. Fixed to prettify a slug-shaped `name` when no `x-anthill-title` is present, leaving
>   legacy pre-standard (already title-cased) names untouched.

- THE SYSTEM SHALL ship a template gallery of ready-made skills that a user can browse, preview, and
  adopt in one click, then edit. The gallery SHALL be seeded from openly licensed skills (Anthropic's
  Apache-2.0 skill library), vendored so it works offline; source-available (non-open) skills SHALL be
  excluded.
- THE SYSTEM SHALL provide a native, in-app guided authoring wizard (guided questions that emit a valid
  `SKILL.md`, with an optional "does it trigger?" check against the local model). It reproduces the
  methodology of the open `skill-creator` without embedding that tool (which is too heavy for
  non-technical users).
- THE SYSTEM SHALL give in-app, local validation feedback on a skill (naming, description, token
  budget, broken links), using the open agentskills.io validation rules as the reference. The reference
  implementation is demonstration-only and SHALL NOT be shipped as-is.
- THE SYSTEM SHALL surface the auto-distillation feature to the user (not only admins) and SHALL
  additionally offer to distil a skill from a chat conversation, not only from scheduled agent runs.
- Skill authoring SHALL conform to the current agentskills.io spec, in which "when to use" is part of
  the `description` field rather than a separate field.
- The skill scope/governance fields (which gate whether a skill is acted on) SHALL be settable in the
  authoring UI; today they are inert, so every hand-made skill ships ungated.

### 4. Effortless, consistent ingestion

> **Status: ingestion-formats half shipped.** Verified against the real code first: `read_file()`
> dispatched only `.pdf`, image extensions, and a UTF-8 plain-text fallback, and `_read_pdf()` was the
> only MarkItDown converter wired up. Fixed: MarkItDown's non-PDF converters are now wired up via a
> new `anthill/multimodal/reader.py::_read_office()` (mirrors `_read_pdf()`'s locked-down
> `MarkItDown(enable_builtins=False, enable_plugins=False)` + single-converter + `StreamInfo` pattern,
> without the PDF path's safety-limit/image-extraction machinery - office-format image extraction is
> future work), covering `.docx`/`.pptx`/`.xlsx`/`.html`/`.htm`. `SUPPORTED_UPLOAD_EXT` in
> `anthill/web/app.py` - a second, independent extension gate - was updated too. Connector imports
> also confirmed independently broken: `wiki_import_connector()` built the page body directly from
> raw connector text instead of running it through the ingest pipeline. Fixed: both `wiki_upload()`
> and `wiki_import_connector()` now call a shared `_ingest_and_propose()` helper, so a Drive/Notion
> import is converted then summarised exactly like an upload, and the two routes cannot drift back
> into different behaviour.
>
> **Status: first-run-queue half shipped.** Verified against the real code first:
> `OrgSettings.local_model_pulling` (a string - the tag downloading, or `""` when done) was already
> the exact flag `GET /models/pull-status` reads for the Models page's own poller; `wiki_upload()`
> had no dedicated handling for it, so an upload made mid-download surfaced as a generic
> `except Exception` -> `?error=ingest` ("Couldn't read that document - is the model running? Try
> again."). Fixed: a new `QueuedUpload` table (`anthill/web/db.py`, additive - `create_all` picks
> it up, no migration entry needed, same as `ProposedSkill`/`TaskRun`) records an upload made while
> `local_model_pulling` is truthy; `wiki_upload()`'s early check saves the bytes durably (per-org,
> under `ANTHILL_FILES_DIR`, NOT the request's ephemeral tempdir) and redirects with a distinct
> `?saved=queued_for_model` (kept separate from the existing `?saved=queued`, which means "waiting
> in the human review queue" - a different concept). A new scheduler tick
> (`_process_queued_uploads_tick` in `anthill/web/scheduler.py`) drains any org's queued rows once
> its model is no longer pulling, ingesting each through the normal pipeline, notifying the
> uploader, and deleting the durable copy either way. The dashboard's existing (but until now
> silent) "preparing your local AI" banner now also links to Wiki/Skills setup and reports queued
> uploads, replacing what was a blank wait with a guided step.

- THE SYSTEM SHALL enable MarkItDown's non-PDF converters so `.docx`, `.pptx`, `.xlsx`, and `.html`
  can be ingested (today only the PDF converter is wired up), and SHALL track a current MarkItDown
  version (2026 releases add aligned-table extraction and scanned-PDF OCR).
- Connector imports (Drive, Notion, and other doc-sources) SHALL run through the same ingest pipeline
  as file uploads (convert then summarise into a structured page), rather than being filed as raw text.
- While the local model is still downloading on first run, THE SYSTEM SHALL present a guided step that
  walks the user through setting up their wiki and skills, and SHALL queue the user's first upload so it
  is processed as soon as the model is ready, instead of failing with an error.

### 5. Knowledge-change audit and digest

> **Status: shipped** (audit half in `683-knowledge-audit-coverage`, digest half in
> `683-knowledge-ledger-and-digest`). This requirement's own framing above ("today only
> `wiki.rejected` and `memory.to_wiki` are logged") was checked against the real code before
> implementing anything and found substantially wrong: `anthill/web/app.py` already called
> `audit.log(...)` for `wiki.approved`, `wiki.upload`, `wiki.upload.confirm`, `wiki.import_connector`,
> `wiki.principles.saved`, `skill.created`, `skill.deleted`, `skill.autolearn_toggle`,
> `skill.proposal_accepted`, `snippet.saved`, `snippet.edit`, `snippet.to_wiki`, plus several
> `memory.*` events - not just the two named above. The real, narrower gaps, verified against the
> current code and fixed: `skill_proposal_reject()` logged nothing at all; `wiki_import_okgf()` (bulk
> OKGF import) logged nothing at all; `approve_review`/`reject_review` logged the same
> `wiki.approved`/`wiki.rejected` event regardless of what kind of change (`WikiReview.kind`: a wiki
> page, a skill, or org principles) was approved, so a reviewer's action on a skill was
> indistinguishable in the log from an action on a wiki page - now split into kind-aware event names
> (`wiki.approved` / `skill.approved` / `principles.approved`, and the `.rejected` equivalents); and
> none of the knowledge-mutation routes captured the request IP - they now do, via the existing
> `_audit_request()` helper that `/logout` and the export routes already used.
>
> The digest half: `anthill/web/digest.py::build_digest()` reads the (now-comprehensive) audit log for
> a `wiki.*`/`skill.*`/`principles.*`/`snippet.*`/`memory.to_wiki`/`memory.promote*` window and returns
> a structured summary (pages changed, skills learned/adopted/deleted/rejected, principles changes,
> snippets captured, promotions between scopes). `OrgSettings.digest_schedule`
> (`off`/`daily`/`weekly`, off by default) + `digest_last_sent_at` drive a scheduler tick
> (`anthill/web/scheduler.py::_digest_tick`) that sends each due org's admins an in-app notification
> and stamps the send time; a small admin card on the Audit page (`POST /settings/digest`) sets the
> cadence.

- THE SYSTEM SHALL write an audit-log row for every knowledge-content mutation: wiki page and skill
  create, edit, delete, and approve/reject, attributed to the acting org and user with the request IP.
  This complements `audit-log-coverage.md`, which covers egress and session events only.
- THE SYSTEM SHALL produce a periodic (daily or weekly, configurable) knowledge digest summarising what
  changed - pages added or updated, skills learned or adopted, and what was promoted between scopes.

### 6. Storage: markdown canonical, plus a database ledger

> **Status: shipped** (`683-knowledge-ledger-and-digest`). Verified before implementing: no DB registry
> existed for wiki pages or skills - only pending-review tables (`WikiReview`/`ProposedSkill`) covering
> changes AWAITING approval, not an index of what currently exists. Added `KnowledgeItem`
> (`anthill/web/db.py`) - a brand-new, purely additive table (`org_id, scope, team_id, kind, slug,
> title, path, review_state, created_at, updated_at, last_editor_id`) - kept in sync from as few choke
> points as possible (`anthill/web/knowledge_registry.py`'s `sync_page()`/`sync_skill()`/`remove()`,
> called from `propose_wiki_write()`'s auto-apply branch, `write_skill()`, `approve_review()`'s
> per-kind branches, and `skills_delete()`), plus a one-time idempotent startup backfill for
> pre-existing content. `AuditLog` gained a nullable `registry_id` column linking an audit row to the
> registry row it acted on, where cheaply resolvable. The markdown files on disk remain untouched and
> stay the sole source of truth; `Workspace.append_log()` (the file-based `log.md` per-workspace
> ledger) is unchanged.

- Wiki and skill CONTENT SHALL remain markdown files on disk (the sovereignty, portability, and OKGF
  export properties are load-bearing and are not to be traded away).
- THE SYSTEM SHALL add a thin database layer over the files for (a) a page/skill registry, (b) a
  revision/audit ledger that records each mutation from section 5, and (c) the data behind the digest.
  The files stay the source of truth; the database is the index and the ledger.

## Out of scope

- The local vision-model licence swap (urgent, tracked separately).
- The larger compute-onboarding redesign (`model-onboarding-and-sovereignty.md`).
- The correctness fixes below ship as a companion batch, not as this program's feature work.

## Companion fix-batch (ship alongside, tracked with this program)

> **Status: shipped.** All four verified against the real code before fixing (not assumed from this
> spec's own description) and covered by new regression tests.

- [x] Memory team-promotion makes the item vanish from the Memory page (manual team promotion nulls the
  owner so it matches neither page query clause, though recall still injects it). Fixed: the page query
  gained a third clause for team-scoped items the user is an active member of.
- [x] Pausing auto-memory does not stop training capture; the off-switch SHALL also stop `record_example`
  so "off" means off. Fixed: the chat route's capture call is now gated on `auto_memory_on()`.
- [x] `wiki_auto_promote` is a rendered, stored toggle that no code reads: wire it or remove it. Removed
  (no design existed for actual auto-promotion criteria; inventing one would be new feature work, not a
  fix) - the form field, template checkbox, and column are gone.
- [x] The stale `okf.py` module docstring (claims OKF is export-only, but pages are now stored in OKGF
  frontmatter on disk). Fixed in place, citing `Workspace.write_page`/`migrate_to_okgf` as the current
  source of truth.

## Acceptance criteria

- A new user can open a short interactive walkthrough for each of Wiki, Memory, and Skills, and each is
  not force-shown again after completion (independently of the others).
- From a chat answer, a user can capture it to the wiki and turn the conversation into a skill using the
  same gesture; a captured snippet is retrieved into a later relevant answer.
- The Skills page shows a non-empty template gallery on a fresh cloud account; adopting a template
  creates a working, scoped skill; the guided wizard emits a spec-valid `SKILL.md`; validation errors
  are shown in-app.
- `.docx`/`.pptx`/`.xlsx` upload produces a structured page; a Drive/Notion import produces the same
  kind of structured page as an upload, not raw text.
- On first run, the first upload made during the model download completes automatically once the model
  is ready; no error is shown for "model not ready".
- The admin Audit log shows create/edit/delete/approve events for wiki pages and skills; a knowledge
  digest is produced on the configured cadence.
- Companion fix-batch: a team-promoted memory remains visible on the Memory page; pausing auto-memory
  stops new training examples; `wiki_auto_promote` either functions or is gone.
