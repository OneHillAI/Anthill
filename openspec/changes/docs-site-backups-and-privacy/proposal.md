# Describe backup and restore, what stays out of answers, the speed line and Linux

Lane: `pillar:privacy`. Priority: medium. Category: documentation.
Full spec, added by the PR that builds this: `docs/specs/docs-site-backups-and-privacy.md`.
Issue #288. Follows #239, #280 and #285.

## Why

A backup is the way back from a lost key, and the guide gives it one sentence. Three smaller points are also missing:
held and rejected uploads are kept out of answers (#284), the speed line under an answer (#222), and what the Linux row
of the platform page means.

## What changes

`docs/setup.md`: a "Back up and restore" section. `docs/using-chat-and-knowledge.md`: the not-used-in-answers rule with
its caveat, and the speed line. `docs/platform-support.md` (repo only): a separate Linux row.

## Decisions (taken by saying "build the PR")

The recommended option of each: tell a Solo reader the page is at `/backup`; state the rule with one sentence for older
uploads.

## Guardrails (do NOT touch)

- No app code, templates or tests. The missing Solo link to the Backup page is an app matter.
- Remote Ollama (#247) is not described; the item records are not described.
- No claim beyond the code. The publish whitelist, existing headings and links stay unchanged.

## Impact

Two published pages and one repo page under `docs/`, one spec, this folder and a changelog fragment. No product code.
