# Spec: Audit-log coverage for session end and data export

Status: implemented. Lane: `pillar:privacy`. Issue: #451 (secondary coverage).

## Problem

The audit log records sign-ins but not their counterparts. Two categories of security-relevant
events go unrecorded, which is a gap for a privacy-first product where an admin needs a complete
account of who did what:

1. **Session end.** `/logout` clears the session cookie and redirects, but writes no audit row, so
   the Audit log shows sign-ins with no matching sign-outs.
2. **Data leaving the perimeter.** The export/download endpoints emit data to the user's machine but
   are not audited: the training-dataset export (`/training/export`), created-file downloads
   (`/files/{name}`), and the wiki OKGF export (`/wiki/export.okgf.tgz`). "Data left the perimeter"
   is exactly what a privacy-conscious admin needs logged.

(The primary half of #451, pre-auth events logged with `org_id=NULL`, was fixed separately in #454.
Role changes are already audited via `user.role_changed`, so they are out of scope here.)

## Requirements

- A successful sign-out writes a `user.logout` audit row attributed to the acting user's org and
  user id, with the request IP. A request with no valid session writes nothing (idempotent logout).
- Each successful data export writes an audit row attributed to the acting org and user, with the
  request IP and a short detail of what left:
  - `/training/export` -> `training.export` (quality + example count);
  - `/files/{name}` -> `file.download` (the file name); a 404 writes nothing;
  - `/wiki/export.okgf.tgz` -> `wiki.export` (scope, and team id when scoped to a team).
- Only genuine egress is logged. Internal editor fetches (e.g. `/skills/{slug}/raw`, which loads a
  skill into the edit form) are not exports and are not audited.

## Acceptance criteria

- After signing out, the admin Audit log shows a `user.logout` event for that user/org.
- After each export, the admin Audit log shows the corresponding event with the actor and IP.
- A logout with no session, and a download of a missing file, produce no audit rows.
- Covered by `tests/test_audit_export_logout.py` (model-free; no backend required).
