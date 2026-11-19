# A Solo account has no link to the Backup page; the desktop app cannot reach it at all

Lane: `pillar:privacy`. Priority: medium. Category: bug (missing navigation).
Full spec, added by the PR that builds this: `docs/specs/solo-backup-link.md`.
Issue #301.

## Why

`/backup` is open to a Solo owner, but every link to it was on an organisation page, and the desktop app has no address
bar. Since the key moved to the Keychain the backup is the way back from a lost key.

## What changes

- A **Backup** card on Settings, This device tab, for an admin, with a link to `/backup`.
- `docs/setup.md` names the new route; the Solo/Org separation spec records it.
- A test that a Solo account finds the link and a non-admin does not.

## Decision (answered 2026-11-19)

This device tab, as recommended in the issue.

## Guardrails (do NOT touch)

- The Backup page, its routes and rules; the organisation pages and their links; the Solo dashboard.

## Impact

One template block, one test, the setup guide sentence, two spec lines, a spec, this folder and a changelog fragment.
