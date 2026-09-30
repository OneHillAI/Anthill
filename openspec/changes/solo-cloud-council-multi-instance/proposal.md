# Solo "Your cloud" council: multi-instance provisioning

Full spec: `docs/specs/solo-cloud-council-multi-instance.md`.

## Why

Solo's "Your cloud" self-provisioned compute tier (RunPod/Lambda) only ever stood up ONE GPU instance:
`_apply_solo_compute`'s `compute == "cloud"` branch provisioned the lead alone via the legacy singleton
`OrgSettings` columns; picking two or three models in the council builder while on "Your cloud" silently
accepted the extra picks but never provisioned them. The infrastructure to provision one council
reviewer already existed and is already live for org accounts
(`provision_council_member`/`teardown_council_member` in `anthill/web/provision_run.py`, wired into the
org admin's per-member Provision/Teardown buttons) - it just wasn't wired into Solo's compute chooser.
The founder decided this should ship specifically on Solo's "Your cloud" tier (not the Advanced/BYO-
cloud-account stubs), with no minimum model-size gate - a real council is meant to be the performant
baseline, not a large-model-only feature.

Investigating how to trigger every member's provisioning together (Solo has one "go" action, unlike the
org admin's one-button-per-member flow) surfaced a real, separate bug: `provision_org`, `teardown_org`,
`provision_council_member`, and `teardown_council_member` each hold a DB session's in-memory read of
`org_council_members` across the ENTIRE duration of a live provisioning call (real GPU spin-up, can be
minutes), then write the full column back at the end. Two of these calls running concurrently - which
Solo's simultaneous trigger makes the *expected* case, not a rare one - is a lost-update race: whichever
commits last silently overwrites whatever the other had already committed to a different member's slot.
This was fixed first (a prerequisite for this feature being safe to ship), since without it, Solo's
multi-instance council would routinely lose a member's provisioned status the moment its GPU finished
booting while another member's was still in flight.

## What changes

- `provision_run.py`: `_mirror_lead_into_council` now takes `db` and refreshes `org_council_members`
  from the DB immediately before merging the lead's fields into slot 0, instead of trusting its
  possibly-minutes-stale in-memory value. A new `_write_member(db, cfg, member_index, member)` helper
  does the same refresh-then-replace-one-slot pattern for `provision_council_member`/
  `teardown_council_member`'s writes (every write site, including the last-resort exception handler).
  This shrinks the write race's window from "the length of a provisioning call" to a few Python
  instructions - closing it for org-admin's existing multi-member flow too, not just Solo's new one.
- `app.py`: `_apply_solo_compute`'s `compute == "cloud"` branch now builds a full `org_council_members`
  list (index 0 = lead, same as the existing "provider" branch's multi-model pattern) whenever 2-3
  models are picked, with each member's `params_b`/`gpu_tier` derived from the catalog. Index 0 keeps
  using the existing `_start_cloud_provision` -> `provision_org` path unchanged; each reviewer (index
  1+) is handed to `provision_council_member` on its own background thread, mirroring the org admin
  route's exact call shape (`expected_provider`/`expected_model` snapshot to guard a mid-flight
  reorder/removal race). Existing single-model behavior and both callers' return-code handling are
  unchanged.

## Guardrails (do NOT touch)

- No change to the org admin's own provisioning routes or UI (`/settings/organization/council/
  provision`/`.../teardown`) - they call the same now-fixed `provision_council_member`/
  `teardown_council_member` functions and get the concurrency fix automatically.
- No 120B+ (or any) minimum-model-size gate on this path - explicitly out of scope per the founder's
  decision.
- No cost-estimation/tracking mechanism added - none exists anywhere in this codebase; a pre-commit
  cost disclosure, if any, is a compute-chooser UI concern, not this change's.
- No change to the Advanced/BYO-cloud-account stubs, or to `_start_cloud_provision`'s own single-model
  logic for index 0.
