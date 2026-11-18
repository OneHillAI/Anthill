# Fix five statements on docs.anthill.run that no longer match the app

Lane: `pillar:platform`. Priority: medium (published pages say things the app does not do; nothing is broken). Category: documentation.
Full spec, added by the PR that builds this: `docs/specs/docs-site-five-wrong-statements.md`.
Issue #239. Found by the daily docs check of 2026-11-17. The app-side wording bug behind item 2 is #241.

## Why

Five statements on the published pages no longer match what the app does: the first-launch screens
(`docs/setup.md`), where an organisation is created (`docs/USING_ANTHILL.md`), Google Drive as a connectable tool
(three pages), what an upload becomes (two pages) and the semantic cache (`docs/how-it-works.md`). The evidence for
each is in the spec.

## What changes

Edit only the five published pages so each statement matches the app, in plain words and the surrounding voice, with
no new pages or sections.

## Guardrails (do NOT touch)

- No app code, templates or catalog. The first-run page's own "from the dashboard" wording is #241.
- `docs-site/scripts/collect-docs.js` and the publish whitelist are unchanged; no internal doc is published.
- No new behaviour is documented here, and the unverified upload limits stay out.
- Headings and links between pages are unchanged.

## Impact

Five markdown files under `docs/`, one spec, this folder and a changelog fragment. No product code.
