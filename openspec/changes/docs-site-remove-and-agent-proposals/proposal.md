# Describe removing a document or a page, and agent-proposed pages waiting for review

Lane: `pillar:knowledge`. Priority: medium. Category: documentation.
Full spec, added by the PR that builds this: `docs/specs/docs-site-remove-and-agent-proposals.md`.
Issue #300. Follows #239, #280, #285 and #288.

## Why

#287 (remove a document or a page, with a way back for 30 days) and #295 (an agent's page waits for review) changed
what a person sees, and the published pages describe neither.

## What changes

`docs/using-chat-and-knowledge.md`: a paragraph on removing, and a clause that Needs review also holds agent-proposed
pages. `docs/using-automation.md`: how an agent's page waits for review. No new pages.

## Guardrails (do NOT touch)

- No app code, templates or tests; no operator tools; the publish whitelist, headings and links unchanged.
- `docs/setup.md` stays as it is: its Solo backup sentence waits for #301.

## Impact

Two published pages under `docs/`, one spec, this folder and a changelog fragment. No product code.
