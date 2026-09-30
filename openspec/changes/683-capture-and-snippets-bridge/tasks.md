# Tasks

- [x] Ground every claim (save_snippet never touches the wiki, snippet_to_wiki defaults to org scope,
      ask.py never references Snippet, the personal-scope auto-apply mechanism) against the real code
      before designing the fix - found the planning pass's `_can_review()` claim doesn't match the real
      code (the function is `_can_approve()` and it isn't why personal writes auto-apply; the real
      reason is `outline_change()`'s scope-gated skip of the model review pass).
- [x] Extract `_snippet_wiki_body()` from `snippet_to_wiki()`, shared with `snippet_save()`.
- [x] `snippet_save()` now also calls `propose_wiki_write(..., target_scope="personal", ...)` and
      stamps `snip.wiki_slug` immediately.
- [x] `snippet_to_wiki()` reuses the same helper (and therefore the same slug once already set).
- [x] Add `POST /skills/draft-from-chat`, reusing `_distil_memory_from_chat()`'s transcript shape and
      the existing `draft_skill()`.
- [x] `chat.html`: "Turn into a skill" button + `turnIntoSkill()`; tightened `commitSnip()` status copy.
- [x] `skills.html`: shared `fillSkillForm()` helper (used by `draftSkill()`, `editSkill()`, and the new
      `?draft=1` sessionStorage prefill); persistent discoverability note.
- [x] Tests: `tests/test_snippet_wiki_bridge.py` (capture -> retrievable via `ask.py`'s page-loading
      path; slug stays consistent across personal -> org promotion even after a tag edit; a flagged
      write still saves the snippet), `tests/test_skill_draft_from_chat.py` (same shape as
      `/skills/draft`; graceful fallback; owner-scoped). `tests/test_snippets.py` needed no changes -
      the wiki write is in the route, not in `save_snippet()`, which those tests call directly.
- [x] Update `docs/specs/knowledge-onboarding-and-guidance.md`: requirement 2 and the "Concept
      simplification" section marked shipped, with the `_can_review()` correction documented.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy` (154 source files, clean),
      full `pytest` (2131 passed, 7 pre-existing skips).
- [x] Live-verify in the browser.
- [x] Add changelog.d fragment.
- [ ] Commit (OneHill-Dev-Agent bot identity, DCO sign-off, `Agent:` trailer), push, open PR via the
      app-token bot identity with the `pillar:knowledge` label + `## Disclosure` + `## Spec` sections.
      Monitor check-runs + `asdd/review` to green; merge only on explicit "merge #N" instruction.
