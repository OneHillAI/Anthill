# Spec: Backup and restore need the install owner

Status: implemented. Lane: `pillar:privacy`. Depends on `docs/specs/install-owner-controls.md` (and through it on
`docs/specs/invite-only-signup.md`).

## Problem

A backup covers the whole install, not one organisation: the database, every organisation's wiki and files, and
the install's keys. Making one (`GET /backup/export`) and restoring one (`POST /backup/restore`) were open to the
admin of any single organisation. They belong with the other install-wide controls.

## Requirements

- `GET /backup/export` and `POST /backup/restore` follow the install-wide rule (`install_scope_allowed`): the
  install owner always; anyone else only on a server that is local (the desktop app, Remote access off) and
  from the machine itself. Anyone else gets 403 and nothing is built or changed.
- The Backup page shows the download and restore controls, the data folder, the model list and the snapshot list
  only to someone who passes the rule. Others see one line: a backup covers the whole install, not one
  organisation, so the install owner makes and restores them.
- On a desktop with Remote access off nothing changes for the person at the machine.

## Acceptance criteria

- Install owner over the network: can download a backup and restore one.
- Another organisation's admin and a member: 403 on both, the data is untouched, the page has no controls and
  does not show the data folder.
- A local request on a marked desktop by any admin: allowed. With Remote access on, or without the desktop
  mark, a request that looks local is refused for a non-owner and still allowed for the owner.

## Tests

`tests/test_backup_install_owner.py`.
