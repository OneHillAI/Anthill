# Cleanup outdated comments and docstrings

Full spec: `docs/specs/cleanup-outdated-comments.md`.
Issue: Fixes #43.

## Why

Comments and docstrings in 7 locations drifted out of sync with recent changes:
- `_sidebar.html`: Stale reference to "delete control" only (pin and rename were added).
- `_sidebar.html` & `personalize.html`: Missing `Integrations` tab in settings summaries.
- `_knowledge_tabs.html`: Stale count of 4 surfaces instead of 5 (Suggestions tab added).
- `chat.html`: Options panel comment mentions "agent" override, which was retired in #421.
- `cli.py`: Workspace `init` docstring omitted `skills/` and `principles.md`.
- `tools.py`: IMAP email reader labelled as stub, and false claim that `ANTHILL_EMAIL_*` enables sending.
- `SOURCES.md`: Referenced non-existent `docs/CODE_AND_DOCS_STANDARDS.md` instead of `AGENTS.md`.

These stale comments confuse agents when reading and writing code.

## What changes

- Update template Jinja/JS comments in `_sidebar.html`, `personalize.html`, `_knowledge_tabs.html`, and `chat.html`.
- Update docstring and comments in `anthill/cli.py` and `anthill/agent/tools.py`.
- Correct documentation reference in `anthill/skills_gallery/SOURCES.md`.
- Add test coverage in `tests/test_outdated_comments.py` verifying docstring and comment consistency.

## Guardrails (do NOT touch)

- Do not alter runtime behavior, route parameters, or template DOM structure.
- Plain hyphens only (no em/en dashes).
