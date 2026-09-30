# Phase 3 of #683: skills guided authoring

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` requirement 3 found Skills to be the worst-guidance
surface: a cloud user typically lands on an empty page, the governance/scope fields are hidden even
though enforcement is real, and auto-distillation only ever surfaced to admins. Verified each claim
against the real code before building, per this session's practice:

- `anthill/agent/skills.py::skill_md()` really did write `when_to_use` only into `x-anthill-when-to-use`,
  not into the base `description` field the current agentskills.io spec requires.
- `anthill/agent/executor.py`'s scope gate (`all(sc in self.identity.scopes for sc in (s.scopes or
  []))`) really is enforced already - the gap was purely that `skills.html`/`skills_create()` never
  exposed or accepted a `scopes` field, so every hand-authored skill silently shipped unrestricted.
- `anthill/web/scheduler.py`'s `_distil_skill_from_agent` really is the only call site of
  `distil_skill()` - chat had no equivalent (shipped separately in phase 2's
  `POST /skills/draft-from-chat`).
- The "Learn skills from agent runs" banner in `skills.html` really was admin-gated in its entirety.
- One spec claim did NOT hold up: "does it trigger... against the local model" does not match how
  skill matching actually works - `executor.py`'s real run-time selection is `match_skills()`'s cheap
  keyword overlap, never a model call. Building a separate, model-judged trigger check would have been
  LESS faithful to reality, not more, so this reuses `match_skills()` directly instead.

## What

- **Conformance fix**: `skill_md()` folds `when_to_use` into the base `description` field when both are
  given ("`<description>` Use when: `<when_to_use>`"), while still also emitting
  `x-anthill-when-to-use` unchanged. `parse_skill_md()` also now prettifies a genuinely-conformant
  third-party `name` (a lowercase-hyphenated slug) into a display title instead of showing it verbatim
  - a real, previously-latent bug this PR's own gallery work exposed (nothing before this PR ever
    loaded a real external skill with no `x-anthill-title`).
- **Governance scopes exposed**: a scopes checkbox group in `skills.html` (the same `_ALL_SCOPES`
  vocabulary as `AgentIdentity`), threaded through `skills_create()` into `write_skill()`/`skill_md()`,
  and returned by `/skills/{slug}/raw` for the edit-prefill.
- **Local validation**: `anthill/agent/skills.py::validate_skill()` + `POST /skills/validate`, called
  client-side on every save attempt and again server-side inside `skills_create()`.
- **"Does it trigger?" check**: `POST /skills/check-trigger`, a thin wrapper around the real
  `match_skills()`.
- **Vendored template gallery**: `anthill/skills_gallery/` (4 entries: `brand-guidelines`,
  `frontend-design`, `internal-comms`, `theme-factory`), each independently verified Apache-2.0 against
  `github.com/anthropics/skills` (see `anthill/skills_gallery/SOURCES.md` for the full audit trail).
  `load_gallery()` (license-filtered), `GET /skills/gallery`, `POST /skills/gallery/{slug}/adopt` (same
  `write_skill()`/review-gate path as a hand-made skill - no bypass).
- **Auto-distillation visibility**: the informational half of the "Learn skills from agent runs" banner
  now renders for every user; only the Pause/Resume control stays admin-gated.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 3 (skills: guided and semi-automated),
marked shipped with the corrections above documented inline.

## Stacking note

This branch (`feat/683-skills-guided-authoring`) is stacked on phase 2
(`feat/683-capture-and-snippets-bridge`, PR #694) - it was branched from that branch's tip before #694
merged, so this PR's diff currently includes phase 2's commits too. It will shrink to just this phase's
commits once #694 merges into `main`. Please merge #694 first.
