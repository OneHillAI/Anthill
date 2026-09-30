# Tasks: PR #661 Tier 3 warm pool + visible private-chat fallback note (completed)

## Build steps (completed)

1. `anthill/hosting/runpod_provision.py`: added `min_workers: int = 0` to the `RunpodClient` Protocol,
   `_RealRunpodClient.create_serverless_endpoint` (used in place of the hardcoded `workersMin: 0` in the
   GraphQL mutation - edited the existing inline template/endpoint creation code, NOT a nonexistent
   `_ensure_template` helper the dev-council's draft invented), and `RunpodLiveProvisioner.provision`
   (passed through to the client call).
2. `anthill/web/db.py`: added `OrgSettings.org_warm_workers` column + `_ensure_columns` migration entry.
3. `anthill/web/provision_run.py`: `_provider_provision_kw` passes `min_workers` for RunPod only, only
   when `org_warm_workers > 0`.
4. `anthill/web/templates/settings_organization.html` + `anthill/web/app.py`'s `settings_org_post`: new
   form field (RunPod-only visibility via the real `syncProvider()` JS function - not the nonexistent
   `updateProviderFields()` the draft invented), saved with a clamp to `[0, 50]`.
5. `anthill/web/templates/chat.html` + the chat route (`anthill/web/app.py`): `is_org_mode` context var
   (reusing `planes.is_org_mode(cfg)`), `IS_ORG_MODE` JS constant, new `setSoloOrgFallbackStatus()`
   function and `applyPlaneStatus` branch - using the REAL `plane-banner` (hyphenated) element id, not the
   draft's `plane_banner` (underscored, would have thrown `TypeError: Cannot set properties of null`).
6. Fixed FOUR existing fake `RunpodClient` test doubles across the suite (`tests/test_runpod_provision.py`,
   `tests/test_provision_ui.py`, `tests/test_wiki_host.py`, `tests/test_council_provision_run.py`) to
   accept `min_workers=0` - found only by running the full test suite, not just this change's own tests.
7. New tests: GraphQL-mutation-level assertions for `min_workers` (real GPU tier keys, not the draft's
   invented `"a40"`); `_provider_provision_kw` RunPod/Lambda/zero-vs-nonzero cases; Settings round-trip +
   clamp (matching the real `302`/`follow_redirects=False` convention, not the draft's vague `{200, 303}`);
   chat-route rendering tests for `IS_ORG_MODE` true/false and the unchanged team/org-plane gating.
8. `docs/specs/local-vs-frontier-capability-roadmap.md` updated in this same PR with a "Status: done" note
   under Tier 3, explaining the re-scoping.
9. `ruff check`, `ruff format --check`, `mypy`, full test suite: 1980 passed, 7 skipped, 0 failed.

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section.
