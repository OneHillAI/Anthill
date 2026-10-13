# Spec: The org wiki is stored per organisation

Status: implemented. Lane: `pillar:privacy`.

## Problem

`workspace_for("org")` returned one folder (`ANTHILL_ORG_WIKI`) for the whole install, whatever organisation the
caller belonged to, and the org-scope wiki routes did not look at the caller's organisation. A server with more
than one organisation therefore shared one org wiki between them. An organisation's wiki should be its own.

## Requirements

### Where the org wiki lives
- `workspace_for("org", org_id=...)` returns the wiki of that organisation. `org_id` is required: without it
  `workspace_for("org")` raises `ValueError`, because leaving it out would pick another organisation's folder.
  Only `org_wiki_root(None)` and `legacy_org_wiki_workspace()` give the legacy folder, for code that moves or
  inspects that folder itself.
  - The organisation recorded as the owner of the existing folder keeps using `ANTHILL_ORG_WIKI` in place, so an
    install with one organisation changes nothing on disk. Before an owner is recorded, organisation 1 is the
    owner.
  - Every other organisation uses `org-wikis/org-<id>`, a sibling of `org-wiki` (`ANTHILL_ORG_WIKIS`
    overrides the location, and is set per profile with `ANTHILL_ORG_WIKI`). It is a sibling, not a child, so
    exporting or backing up one organisation's folder does not include another's. That holds for new content;
    what was written into the shared folder before this change stays in it (see below).
- The owner marker `ANTHILL_ORG_WIKI/.owner-org` is created exclusively: the owner is written to a temporary
  file with a name no other process shares and hard-linked into place, so the first claim stays and a second
  cannot overwrite it; a filesystem without hard links falls back to `O_EXCL`. A failure is logged, not raised.
- The owner is recorded once and never changed:
  - At start-up, as the organisation of the first admin (the account the knowledge registry has always
    attributed the folder to), else the oldest organisation.
  - After the first organisation of a fresh install has been committed (the sign-up form and the OAuth first
    account).
  - The start-up steps (record the owner, the notice below, migrate the personal wiki) each have their own try
    block, so one failing never skips the next.

### An install that has several organisations when it is upgraded
- Until now every organisation wrote into the one folder. It **stays with the organisation recorded as its
  owner**, and the other organisations **start with an empty org wiki**. Nothing is moved, copied or deleted,
  and nothing is guessed about who wrote what.
- At the first start after the upgrade, when there is more than one organisation and the folder has pages:
  - the owner's admins see a plain notice at the top of the org wiki page: before this update every
    organisation used the same organisation wiki, it stays with their organisation, the others now start with
    an empty one, and nothing was deleted;
  - one audit row per organisation (`org_wiki.shared_before_upgrade`) is written with that organisation's
    `org_id`, saying whether it is the owner or another organisation and how many pages the previous shared
    folder holds;
  - the server log gets one line with counts only. No page name is written to the log or the audit.
  - `ANTHILL_ORG_WIKI/.upgrade-notice` records that the check ran, so it happens once. A one-organisation
    install gets that file and `.owner-org` and nothing else; an organisation created later is told nothing.
- A later host-side tool may move pages to an organisation on request. It is not part of this change.

### Everything that acts for an organisation names it
- Every code path that serves a signed-in user passes the caller's organisation: the Pages list and page view,
  raw source download, export and import, the review queue and its approval, uploads queued for ingest, skill
  writes (the gallery adopt route now passes `db` and `org_id`, so every adoption writes a knowledge registry
  row, as the other skill routes do), chat and agent grounding (including project parent wikis), scheduled tasks
  and agent runs, the dashboard counts, the MCP org-brain lookups and the knowledge registry backfill.
- The semantic page index covers every organisation's folder.
- A backup is of the whole install: it includes `org-wiki` and `org-wikis`. Restoring an archive made before
  this change (no `org-wikis`) moves the `org-wikis` already on the machine aside
  (`org-wikis.before-restore-<time>`) instead of mixing two states; the restore result names it and the backup
  page and the command-line restore print it. Who may take or restore a backup is a separate matter and does
  not change here.
- The backend page shows the folder of the signed-in organisation.
- The upgrade check (notice and audit rows) runs when the folder's owner is recorded, and only for an install
  that already had several organisations then. On a fresh install the owner is recorded at the first sign-up,
  so later organisations are never told about a shared folder that never existed. The marker is created
  exclusively before any audit row is written. An empty or unreadable owner marker is replaced on both the
  hard-link and the `O_EXCL` path.
- The folder owner is an organisation, separate from the install owner, and does not follow a change of install owner.
- Out of scope: the mesh orchestrator (`anthill/orchestrator/app.py`) is a separate service with its own org
  wiki path and is not made per organisation here.
- Nothing else about scopes changes: team and personal wikis are keyed by their own ids as before.

## Acceptance criteria

- A second organisation gets 404 for a page and a raw source file that exist only in the first organisation's
  wiki, its Pages list shows only its own pages, and its export holds only its own pages.
- A page written by the second organisation is stored under `org-wikis/org-<id>` and never in `org-wiki`.
- Upgraded install with two organisations: the owner still reads every old page, the other organisation sees
  none of it, its new writes are separate, and nothing in the old folder moved.
- The notice shows to the owner's admin and not to the other organisation; the audit rows are one per
  organisation with their ids and carry counts only; the log carries no page name; it all happens once.
- A one-organisation install: nothing moves, its cache stays, and the only additions are `.owner-org` and
  `.upgrade-notice`; no notice, no audit row.
- The owner marker survives two claims at once, a filesystem without hard links, and a half-made marker.
- No call in the shipped code picks an org folder without naming the organisation: a walk of every module's
  syntax tree covers `workspace_for`, `write_skill` with a scope, `run_wiki_workspace`, `org_wiki_root` and direct
  `Workspace` construction (it checks calls, not reads of `ANTHILL_ORG_WIKI`), and is itself tested against a
  tree with each bad form: a missing `org_id`, `org_id=None`, `**kwargs`.
- Adopting a gallery skill for the org as the second organisation's admin writes into that organisation's
  folder, not the owner's.
- Backup data paths and the page index include the per-organisation folders; a real backup and restore round
  trip keeps both; restoring an older archive moves `org-wikis` aside and names it.

## Tests

`tests/test_org_wiki_per_org.py`, and a changed call in `tests/test_tiers_context.py`: a system run with no user
(`context_workspaces(user_id=None, plane="org")`) now names its organisation with `org_id=1`, because the org
scope requires one.
