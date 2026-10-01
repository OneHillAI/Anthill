# Cleanup outdated comments and docstrings

Status: ready for review. Lane: `chore`.
Issue: Fixes #43.

## Problem

Several comments and docstrings across templates and backend modules drifted after recent PRs and
refactors. Because AI agents and human contributors rely on comments and docstrings to navigate
and reason about the codebase, stale comments cause confusion or misleading assumptions.

Specifically:
1. `anthill/web/templates/_sidebar.html` (lines 111-112) described the conversation hover row as
   "the link + a delete control", but the row now holds three actions: Pin, Rename, and Delete.
2. `anthill/web/templates/_sidebar.html` (lines 220-224) and `anthill/web/templates/personalize.html`
   (lines 120-126) described the Settings tabs without listing the Integrations tab added in PR #6,
   and claimed "This device" was the last tab before Organisation.
3. `anthill/web/templates/_knowledge_tabs.html` (lines 1-5, 15-17) referred to switching between
   "four surfaces" and "four separate per-surface auto-tours", but the Knowledge hub now includes
   five tabs (Wiki, Snippets, Memory, Skills, and Suggestions).
4. `anthill/web/templates/chat.html` (line 505) described the Options panel as "the web/agent/scope
   overrides", but the manual Agent mode toggle was retired in #421 and the panel now only toggles
   Web search and Knowledge scope.
5. `anthill/cli.py` (line 97) `init` command docstring listed `(raw/ inbox/ wiki/ index.md log.md SCHEMA.md)`
   omitting `skills/` and `principles.md`, which `Workspace.init()` generates.
6. `anthill/agent/tools.py` (lines 270, 334) labelled IMAP reading as "stubs" even though `_read_emails`
   is a live client, and claimed configuring `ANTHILL_EMAIL_*` enables sending even though no sending
   tool exists in `tools.py` (outbound email is handled by `mailer.py` via `ANTHILL_SMTP_*`).
7. `anthill/skills_gallery/SOURCES.md` (line 50) referenced `docs/CODE_AND_DOCS_STANDARDS.md`, which does
   not exist; the hyphen/dash rule is in `AGENTS.md`.

## Fix

Update all 7 files so that their comments, docstrings, and cross-references accurately reflect the
current system implementation. Add automated assertions in `tests/test_outdated_comments.py` to
prevent future drift.

## Acceptance criteria

- `_sidebar.html` comment reflects pin, rename, and delete actions.
- `_sidebar.html` and `personalize.html` comments list the Integrations tab.
- `_knowledge_tabs.html` comment reflects the 5 Knowledge surfaces.
- `chat.html` Options panel comment omits the retired agent toggle.
- `cli.py` `init` docstring includes `skills/` and `principles.md`.
- `tools.py` accurately labels the email reader and removes the false sending claim.
- `SOURCES.md` points to `AGENTS.md`.
- `tests/test_outdated_comments.py` passes.
