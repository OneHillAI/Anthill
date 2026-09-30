# Tasks

- [x] Verify each of the 4 claimed bugs against the real code before fixing.
- [x] Fix memory team-promotion visibility (`memory_page`'s query gains a team clause).
- [x] Fix auto-memory pause not stopping training capture (`auto_memory_on()` gate on `record_example`).
- [x] Remove dead `wiki_auto_promote` (route, template, DB column, and the 2 tests specific to it).
- [x] Correct the stale `okf.py` module docstring.
- [x] Tests: `tests/test_memory_control.py` (2 new: team-promotion visibility, auto-memory-off stops
      capture), `tests/test_org_automation_settings.py` (updated for the removal).
- [x] Update `docs/specs/knowledge-onboarding-and-guidance.md`'s companion fix-batch section to shipped.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2112 passed).
- [ ] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR **via the
      app-token bot identity** (not a personal account) with the `pillar:knowledge` label +
      `## Disclosure` checkbox + `## Spec` section. Monitor check-runs + `asdd/review` to green; merge
      only on explicit "merge #N" instruction.
