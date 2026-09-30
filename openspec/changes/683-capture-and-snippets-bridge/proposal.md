# Phase 2 of #683: snippet-to-wiki capture bridge + turn a chat into a skill

## Why

`docs/specs/knowledge-onboarding-and-guidance.md`'s "Concept simplification" section and requirement 2
found that a saved snippet is a silo: it becomes a personal gold training example, but it never reaches
the wiki unless the user separately discovers and clicks "-> Wiki" on the `/snippets` list - and even
then that click defaults to proposing an ORG-scope page. A user who "collects" a snippet expecting it to
help later answers is let down, because `anthill/wiki/ask.py` retrieves from `Workspace.pages()` (files
on disk) and has never known about `Snippet` rows at all. Skills has a parallel gap: the only way to
create one is to describe it from scratch on the Skills page - there is no way to turn a chat you just
had into a skill, even though the drafting model (`draft_skill()`) and the guided wizard already exist.

Verified against the real code before building (not assumed from the spec's own wording):
- `anthill/web/snippets.py::save_snippet()` really never touches the wiki; `grep -rn "Snippet"
  anthill/wiki/` really returns nothing.
- `POST /snippets/{id}/wiki` -> `snippet_to_wiki()` really defaults `target_scope` to `"org"`.
- One planning-pass assumption did NOT hold: there is no `_can_review()` function, and personal-scope
  wiki writes do not auto-apply because "the proposer approves their own item." The real mechanism is
  `outline_change()` (`anthill/wiki/review.py`): for `scope == "personal"` it skips the model review
  pass entirely and applies mechanical checks only (dangling `[[links]]`, a near-duplicate title) - "a
  user's private notes apply immediately." The actually-named `_can_approve()` only decides who may
  approve an item that got queued in the first place (irrelevant to the common auto-apply case). This
  PR's code comments and the spec's "Concept simplification" section cite the real functions.

## What

- `_snippet_wiki_body(snip) -> tuple[str, str]` (`anthill/web/app.py`): the slug/body-building logic
  extracted from `snippet_to_wiki()` into a shared helper. Reuses `snip.wiki_slug` when already set, so
  a later promotion edits the same page instead of forking a new one if the snippet's tags changed
  in-between.
- `snippet_save()` (`POST /snippets/save`) now also calls the existing `propose_wiki_write(...,
  target_scope="personal", ...)` immediately after saving, and stamps `snip.wiki_slug`. A captured
  snippet is now a real page in the user's personal `Workspace` the moment it is saved - grounded into
  retrieval like any other wiki content - while the existing capture value (red line rationale, gold
  `TrainingExample`, `content_key` corroboration, provenance) is untouched. `snippet_to_wiki()` is
  unchanged in behaviour (still the review-gated path to promote to team/org) but now shares the same
  body-building helper.
- `POST /skills/draft-from-chat` (`anthill/web/app.py`): builds a transcript the same way
  `_distil_memory_from_chat()` does (last ~8 `ChatMessage`s, `"{role}: {content}"` joined) and calls the
  existing `draft_skill(transcript, backend)`, returning the same JSON shape `/skills/draft` returns.
  Owner-scoped like the other conversation routes (a user can't draft from someone else's conversation).
- `chat.html`: a "Turn into a skill" button next to the conversation header (shown once the conversation
  has messages) posts to the new route, stashes the draft in `sessionStorage`, and redirects to
  `/skills?draft=1`. `commitSnip()`'s status copy now says the snippet is grounded into the personal
  wiki immediately (distinguishing the auto-applied vs. queued-for-review case) instead of just "(saved
  for you)".
- `skills.html`: on load, `?draft=1` + a stashed draft opens the `#new-skill` panel and prefills it via
  a new shared `fillSkillForm()` helper (also now used by the existing `draftSkill()`/`editSkill()`,
  instead of three copies of the same four-field assignment), then clears the stashed draft. A
  persistent, low-key line notes the chat capture gesture exists, satisfying the spec's "discoverable
  on the Skills page" requirement.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 2 (one capture pattern across surfaces)
and the "Concept simplification: snippets are self-added wiki elements" section, both marked shipped.
Requirements 1 (walkthroughs, shipped) and 3-6 are out of scope here - phase 3 (this program's next
phase) covers requirement 3 (skills: guided and semi-automated).
