# Solo "Your cloud" council: multi-instance provisioning

## Problem

Solo's "Your cloud" compute tier (self-provisioned RunPod/Lambda, `_apply_solo_compute`'s
`compute == "cloud"` branch) only ever provisioned a single GPU instance: `provision_org()` reads the
`OrgSettings` singleton columns (`org_model`, `org_gpu`, ...) and provisions one model, and only council
slot 0 (the lead) was ever populated by a provision. Picking two or three models in the council builder
while on "Your cloud" silently provisioned the lead alone - the other picks were accepted by the form
but never stood up.

The infrastructure to provision an individual council reviewer already existed and was already live for
org accounts: `provision_council_member()`/`teardown_council_member()`
(`anthill/web/provision_run.py`), wired into the org admin's per-member "Provision"/"Teardown" buttons
(`POST /settings/organization/council/provision` and `.../council/teardown`). Both read/write
`org_council_members[i]` through `_MemberRef`, a read-only adapter presenting one member dict through
the same `cfg.org_*` attribute surface the provisioning helpers (`_spec_from_cfg`, `_provider_provision_kw`,
`_live_client`) already expect - so no provisioning-engine change was needed to serve a Solo account's
council members instead of an org's. The founder decided the multi-instance council should ship
specifically on Solo's "Your cloud" tier (not the Advanced/BYO-cloud-account stubs), with no minimum
model-size gate: the aim is to make a real council the *baseline* for a properly performant Solo build,
not a large-model-only feature.

## Design

**Trigger, not click-through.** Unlike the org admin page (one "Provision" button per member, clicked
individually), Solo's compute chooser is a single "go" action. `_apply_solo_compute`'s cloud branch now
builds all N `org_council_members` entries up front and triggers every member's provisioning together:
index 0 (the lead) keeps using the existing singleton-column path (`_start_cloud_provision` ->
`provision_org`, unchanged); each reviewer (index 1+) gets its own `org_council_members[i]` entry and is
handed to `provision_council_member()` on a background thread, mirroring the org admin route's call
shape (`expected_provider`/`expected_model` snapshotted before threading, to guard against a save
reordering members mid-flight - the exact same race the org route already guards against).

```python
threading.Thread(
    target=lambda i=i, t=t: provision_council_member(
        eng, org_id, i, expected_provider=cloud_provider, expected_model=t
    ),
    daemon=True,
    name=f"anthill-solo-provision-member-{i}",
).start()
```

Each member is built from the catalog the same way the "provider" (hosted-inference) branch already
does for its own multi-model council: `provider`/`model`/`params_b`/`lifecycle="vpc"` set per member,
plus a per-member `gpu_tier` from `_smallest_gpu_for_model` (the same auto-picker the lead already
used). `backend_status` starts at `"planned"`, matching the org admin's own vocabulary for "selected,
not yet provisioned" (`_org_status_after_save`'s fresh-selection outcome).

**No cost gate, no size gate.** Per the founder's decision, there is no 120B+ minimum and no
provisioning confirmation step here - the UI layer (compute-chooser session) is responsible for
disclosing that N models means N billable GPU instances before the user commits, since no cost-tracking
mechanism exists anywhere in this codebase to gate on automatically.

**Return codes are unchanged.** `_apply_solo_compute` still returns `"provisioning"` /
`"cloud_pending"` based on the LEAD's own outcome only (`_start_cloud_provision`'s result) - both
existing callers (`/setup/model`, `/personalize/compute`) redirect on exactly those two codes today,
so no caller-side change was needed. Reviewer provisioning is triggered unconditionally whenever
`len(council) >= 2`, independent of whether the lead's own kickoff succeeded: `provision_council_member`
does its own per-member error handling (writes `backend_status = "error"` onto that member if its own
provider/key checks fail), so a lead that can't start yet doesn't block reviewers from at least
attempting to report their own real status.

## Concurrency fix (prerequisite)

Triggering the lead's and every reviewer's provisioning *at the same time* turned a previously-latent
bug into an always-on one. `provision_org`, `teardown_org`, `provision_council_member`, and
`teardown_council_member` each open their own DB session, read `org_council_members` once at the start
of a call, hold that Python object in memory across a real (multi-minute) provider network call, then
write the *entire* column back at the end. Two of these calls running concurrently - exactly what
Solo's simultaneous trigger now does - is a classic lost-update race: whichever call commits last wins,
silently overwriting whatever the other call had already committed to a *different* member's slot.
Before this change the race existed only if an org admin happened to click two "Provision" buttons in
quick succession; after this change, for Solo, it is the expected interleaving on every multi-model
"Your cloud" save.

Fixed by re-reading `org_council_members` from the DB immediately before each function's final
merge-and-commit, instead of trusting the in-memory value loaded at call start:

- `_mirror_lead_into_council(db, cfg)` (now takes `db`) calls `db.refresh(cfg,
  attribute_names=["org_council_members"])` before merging the lead's fields onto `members[0]`.
- A new `_write_member(db, cfg, member_index, member)` helper does the same refresh, then replaces only
  `members[member_index]` before writing - used by every write site in `provision_council_member` and
  `teardown_council_member` (including the last-resort exception handler).

This shrinks the race window from "the full duration of a provisioning call" to "the few Python
instructions between refresh and commit" - not a lock, but the same pragmatic mitigation this codebase
already uses elsewhere for external-call-shaped state (e.g. the `expected_provider`/`expected_handle`
snapshot guards). A row-level lock or per-member DB rows would close the remaining narrow window
entirely; that is a larger schema change, out of scope here.

## Out of scope

- Advanced/BYO-cloud-account provisioning (AWS/GCP style) - explicitly excluded by the founder's
  decision; this only covers the two live self-provisioners (RunPod, Lambda).
- Cost estimation/tracking or a pre-commit cost-confirmation step - no such mechanism exists anywhere
  in this codebase today; left to the compute-chooser UI to disclose in copy.
- A full concurrency fix (row-level locking / per-member storage) for `org_council_members` - the
  refresh-before-merge mitigation here closes the race window from minutes to milliseconds, which is
  judged sufficient for N <= 3 members; revisit if council size grows.
- Any change to the org admin's own provisioning routes/UI - they already call the same
  `provision_council_member`/`teardown_council_member` functions and benefit from the concurrency fix
  automatically, with no route or template changes needed on that side.
