# Describe the new first-run setup, model list, downloads, Settings and chat page on docs.anthill.run

Lane: `pillar:model`. Priority: medium. Category: documentation.
Full spec, added by the PR that builds this: `docs/specs/docs-site-models-and-setup.md`.
Issue #285. Follows #239 and #280.

## Why

First run no longer offers "Your machine or Your cloud", yet `docs/using-models-and-compute.md` says it does. The new
fit labels, live downloads with cancel, the Settings tabs, the answer source label and the first dashboard are not
described anywhere.

## What changes

Edit four existing pages, no new pages: `docs/using-models-and-compute.md` (first run, provider step, image
understanding, fit labels, downloads), `docs/setup.md` (the four steps), `docs/USING_ANTHILL.md` (Settings tabs,
unsaved changes, "Try this next"), `docs/using-chat-and-knowledge.md` (answer source, linked pages).

## Guardrails (do NOT touch)

- No app code, templates or tests.
- Not documented: the setup and mesh tokens (already in `docs/setup.md`), the chat speed line (unconfirmed), operator
  tools.
- Backups, privacy, remote Ollama and Linux are the next issue.
- The publish whitelist, headings and links stay unchanged.

## Impact

Four markdown files under `docs/`, one spec, this folder and a changelog fragment. No product code.
