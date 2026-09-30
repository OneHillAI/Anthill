# Tasks

- [x] `provision_run.py`: `_mirror_lead_into_council(db, cfg)` refreshes `org_council_members` from the
  DB immediately before merging the lead's fields into slot 0; updated its 3 call sites
  (`provision_org`, `teardown_org`, `_fail`).
- [x] `provision_run.py`: new `_write_member(db, cfg, member_index, member)` helper (refresh, then
  replace only that slot); routed every `org_council_members` write in `provision_council_member` and
  `teardown_council_member` through it, including the last-resort exception-handler write.
- [x] `app.py`: `_apply_solo_compute`'s `compute == "cloud"` branch builds `org_council_members` for a
  2-3 model council (provider/model/params_b/gpu_tier per member, `lifecycle="vpc"`,
  `backend_status="planned"`) and threads `provision_council_member` for every reviewer (index 1+),
  alongside the existing `_start_cloud_provision` call for the lead (index 0). Single-model behavior and
  both callers' (`/setup/model`, `/personalize/compute`) return-code handling are unchanged.
- [x] Tests: `tests/test_council_provision_race.py` (3 tests) reproduce the write race deterministically
  (a fake provider client triggers the OTHER concurrent call mid-flight) and prove they fail without the
  fix and pass with it, in both directions (lead-vs-reviewer, reviewer-vs-reviewer).
  `tests/test_solo_cloud_council_multi_instance.py` (4 tests) cover: a 2-model council builds members
  and provisions the one reviewer; a 3-model council provisions both reviewers; a single-model choice is
  byte-for-byte unchanged from before this change; reviewer provisioning still runs even when the lead's
  own kickoff doesn't start (`cloud_pending`).
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2412 passed, 7
  skipped, no regressions).
