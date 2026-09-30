# Tasks

- [x] Ground the current onboarding/tour mechanism and each surface's existing self-explanation
      against real code before designing the walkthrough mechanism.
- [x] Add `User.completed_walkthroughs` (CSV column, additive, auto-migrates).
- [x] Add `GET /walkthroughs/status` / `POST /walkthroughs/done` routes.
- [x] Add `anthill/web/static/walkthrough.js` (generic per-surface engine) and wire it into
      `base.html`.
- [x] Add step sequences + "Take a tour" affordance to `wiki.html`, `memory.html`, `snippets.html`,
      `skills.html`, anchored to stable ids added to each template's existing elements.
- [x] Tests: `tests/test_walkthroughs.py` (status/done round trip, per-surface independence, CSV
      accumulation, unknown-surface handling).
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2124 passed).
- [x] Live-verify in the browser: confirmed all 4 walkthroughs auto-start on a fresh account, position
      correctly, skip missing-target steps (no connectors configured, no snippets yet), persist
      completion across a server restart, stay independent per surface, and "Take a tour" replays
      correctly after completion. Found and fixed two real bugs in the process (see below).
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR via the
      app-token bot identity with the `pillar:knowledge` label + `## Disclosure` + `## Spec` sections.
      Monitor check-runs + `asdd/review` to green; merge only on explicit "merge #N" instruction.

## Bugs found and fixed during live verification

- The per-page `anthillWalkthrough(...)` call executed before `walkthrough.js` (loaded later in
  `base.html`) had defined the function - deferred each call to `DOMContentLoaded`, matching the
  pattern `tour.js` already uses for the same reason.
- The first spotlight's position was measured immediately after DOM insertion, before layout had
  settled, producing a wildly off-screen position on first paint - deferred the initial `showStep(0)`
  by a double `requestAnimationFrame`.
- Unrelated to the above: firing several concurrent requests at the new routes reproduced a real,
  pre-existing, codebase-wide issue (`_db()` sessions are rarely closed, eventually exhausting
  SQLAlchemy's connection pool). Fixed only the two new routes here (`try/finally: db.close()`,
  matching the ~14 existing call sites that already do this); flagged the systemic gap as a separate
  follow-up rather than expanding this PR's scope.
