# Describe the core page, the Library, page history, the answer panel and the memory changes on docs.anthill.run

Lane: `pillar:knowledge`. Priority: high for timing (before the next beta), medium for content. Category: documentation.
Full spec, added by the PR that builds this: `docs/specs/docs-site-core-page-and-library.md`.
Issue #372. Requested by the Knowledge Dev session; the maintainer said yes on 2026-11-25.

## Why

39 PRs since the check of 2026-11-20 left six published statements wrong (the wiki's three tabs, "the earlier text isn't
kept", pausing memory, what MCP offers, "guides every response", the shared answer index) and eight changes
undescribed (the core page, the agent's remember proposal, the memory archive, the page about an item, richer removal,
the Also given list, history and restore, a failed download).

## What changes

Edit four published pages: `docs/using-chat-and-knowledge.md`, `docs/using-connectors-and-personalization.md`,
`docs/how-it-works.md`, `docs/using-models-and-compute.md`. No new pages.

## Guardrails (do NOT touch)

- No app code, templates or tests; no routes, environment variables or operator commands in the guide.
- No claim of a button or screen that does not exist.
- The publish whitelist, existing headings and links; the frozen impact log.

## Impact

Four published pages, one spec, this folder and a changelog fragment. No product code.
