# Tasks

- [x] Ground every claim (skill_md's non-conformant description, executor.py's real scope
      enforcement, scheduler.py's only distil_skill call site, the admin-gated banner, and the
      spec's own "against the local model" trigger-check wording) against the real code before
      building - found the trigger-check wording doesn't match reality (real matching is keyword
      overlap, not a model call) and a real, previously-latent title-display bug in `parse_skill_md()`.
- [x] Fold `when_to_use` into `description` in `skill_md()`; keep `x-anthill-when-to-use` unchanged.
      Update `tests/test_agentskills_conformance.py` and `docs/AGENT_SKILLS.md`'s worked example.
- [x] Prettify a conformant (slug-shaped) third-party `name` in `parse_skill_md()` instead of
      showing it verbatim as the display title.
- [x] Add a scopes checkbox group to `skills.html`, threaded through `skills_create()` into
      `write_skill()`/`skill_md()`; expose `scopes` via `/skills/{slug}/raw` for edit-prefill.
- [x] `anthill/agent/skills.py::validate_skill()` + `POST /skills/validate`, called client- and
      server-side (inside `skills_create()`).
- [x] `POST /skills/check-trigger`, reusing `match_skills()` directly (no separate heuristic).
- [x] `anthill/skills_gallery/` (4 Apache-2.0-verified entries + `SOURCES.md` audit trail),
      `load_gallery()`, `GET /skills/gallery`, `POST /skills/gallery/{slug}/adopt` (same
      write_skill()/review-gate path as a hand-made skill).
- [x] Un-admin-gate the "Learn skills from agent runs" informational text in `skills.html`; keep the
      Pause/Resume control admin-gated.
- [x] Tests: `tests/test_skill_validation.py`, `tests/test_skill_gallery.py`,
      `tests/test_skill_trigger_check.py`, `tests/test_skill_scopes_authoring.py`; updated
      `tests/test_agentskills_conformance.py`.
- [x] Update `docs/specs/knowledge-onboarding-and-guidance.md`: requirement 3 marked shipped, with
      the trigger-check wording correction and the gallery's honest scope documented.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy` (154 source files, clean),
      full `pytest` (2161 passed, 7 pre-existing skips).
- [x] Live-verify in the browser.
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR via the
      app-token bot identity with the `pillar:knowledge` label + `## Disclosure` + `## Spec` sections,
      explicitly noting the stack on phase 2's PR (#694). Monitor check-runs + `asdd/review` to
      green; merge only on explicit "merge #N" instruction.
