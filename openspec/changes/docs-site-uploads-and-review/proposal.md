# Describe how uploads are read, held and reviewed on docs.anthill.run

Lane: `pillar:knowledge`. Priority: medium. Category: documentation.
Full spec, added by the PR that builds this: `docs/specs/docs-site-uploads-and-review.md`.
Issue #280. Follows #239 and PR #244.

## Why

A dozen PRs of 2026-11-15 to 2026-11-19 changed how a document is read, held, reviewed and used in an answer. The
published pages describe almost none of it, and `docs/setup.md` names a screen ("Wiki > Review") that is now the
"Needs review" tab under Knowledge.

## What changes

Edit the existing pages, no new pages: rename the old review screen in five pages, and in
`docs/using-chat-and-knowledge.md` add the held-upload choices, the full status list with the retry and the PDF limits,
the "text read from it" link and page footers, and how answers can quote the original text.

## Guardrails (do NOT touch)

- No app code, templates or tests.
- No operator-only material: item records, `anthill knowledge inventory`, `ANTHILL_DOC_CONTEXT`.
- No models, setup, backup, privacy, dashboard or Settings text: those are separate issues.
- The publish whitelist, headings and links between pages stay unchanged.

## Impact

Five markdown files under `docs/`, one spec, this folder and a changelog fragment. No product code.
