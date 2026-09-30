# Companion fix-batch for #683 (knowledge onboarding and guidance)

## Why

`docs/specs/knowledge-onboarding-and-guidance.md` names four correctness bugs found during its own
review, explicitly scoped as a companion batch shipping separately from the program's feature work.
Verified each against the real code before fixing (not assumed from the spec's own description).

## What

- **Memory team-promotion visibility**: `memory_promote_team` sets `scope="team"` and clears `user_id`;
  the Memory page's query (`user_id == uid` OR `scope == "org"`) matched neither clause, so a team-
  promoted item silently vanished from the list (recall still injected it into answers - only the list
  view was broken). Added the missing third clause.
- **Auto-memory pause not stopping training capture**: the chat route's `record_example()` call was
  gated only on "not an ephemeral P4 run" - pausing auto-memory did nothing to it, so a user's full
  turns kept being captured as training data (more revealing than a memory item) after they asked
  Anthill to stop remembering things about them. Gated on `auto_memory_on()` too.
- **Dead `wiki_auto_promote` toggle**: rendered and stored, read by nothing. Removed (form field,
  template checkbox, DB column) rather than inventing undesigned auto-promotion criteria.
- **Stale `okf.py` docstring**: claimed OKF was export-only with no on-disk change; `Workspace.write_page`
  has written every page through OKGF frontmatter for a while now. Corrected in place.

## Spec

`docs/specs/knowledge-onboarding-and-guidance.md`'s companion fix-batch section, marked shipped.
