# PR #661 Tier 3: a real warm pool, and a visible note for the silent private-chat fallback

## Why

#661's roadmap named two gaps under "soften the pain when the giant/cold tier IS needed":

1. **Cash the warm-pool IOU.** `anthill/hosting/provision.py`'s `_serverless_steps()` shows the admin a
   planner step promising "Configure scale-to-zero with an optional warm pool to bound cold starts" -
   verified this text exists on `origin/main` today. No such mechanism exists: `runpod_provision.py`'s
   `RunpodLiveProvisioner.provision()` hardcodes `workersMin: 0` in the RunPod GraphQL mutation
   unconditionally - there is no way, today, for an org to actually keep a worker warm.
2. **Extend the local-fallback UX to the Org-plane cold-start case** - re-scoped after two rounds of
   correction this session, since the ORIGINAL framing (as literally described in #661) does not fit the
   real architecture.

## Re-scoping item 2 (two real corrections, in order)

**First correction**: a TRUE team/org-plane chat (grounded in the shared org wiki) genuinely has no local
fallback available - the org wiki is hosted at the same place as the org model, so when that backend is
down, both the model AND the wiki data are unreachable together. There is no separately-hosted "local
copy" of the org wiki to fall back to. The existing "disabled, here's why" behavior for team/org-plane
chats (`chat.html`'s `setOrgChatEnabled`) is therefore CORRECT and is NOT touched by this change.

**Second correction**: a member's PRIVATE chat inside an org account (the "Solo" plane, borrowing the
shared org model but grounded in the member's OWN local wiki, kept ephemeral - `docs/specs/
one-model-per-account-solo-in-org.md`, confirmed still the founder's decision, NOT being reversed) already
has a WORKING fallback, verified in `anthill/web/plane_routing.py`: when the org endpoint is unreachable,
it silently falls through to the local model (the personal wiki is already local to the device, so nothing
about the DATA needs to change - only which model answers). Verified the frontend (`chat.html`'s
`applyPlaneStatus`) does not even attempt to gate this case (`convPlane !== 'org'` short-circuits to
"always enabled" for a Solo-plane conversation, even one running on a borrowed org model) - so the
fallback already works, completely invisibly. There is no banner, no indication a member has quietly
dropped to a weaker local model for this private chat, unlike the Solo-with-own-VPC case which already
shows "Using your local model (cloud model unreachable) - same wiki, lower quality...".

**What this change actually does for item 2**: add the SAME visible, non-blocking informational note for
this one remaining silent case - no new gating, no new prompt-and-choose interaction (unlike the VPC case,
there is nothing to choose; it already falls back automatically), just visibility into behavior that
already happens.

## What already exists and is REUSED

- `anthill/hosting/provision.py`'s `ProvisionSpec`, `_serverless_steps()` - the planner step text stays;
  this change makes it true rather than aspirational.
- `anthill/hosting/runpod_provision.py`'s `RunpodLiveProvisioner.provision(spec, *, max_workers=..., ...)`
  - `max_workers` is ALREADY a separate keyword parameter (not part of `spec`); `min_workers` follows the
    exact same convention, not bolted onto `ProvisionSpec`.
- `anthill/web/provision_run.py`'s `_provider_provision_kw(cfg)` - already derives RunPod-specific launch
  kwargs (`gpu_ids`, `hf_token`) from `OrgSettings` fields; `min_workers` added here the same way.
- `anthill/web/db.py`'s `org_gpu`/`org_provider` fields and `settings_organization.html`'s GPU-tier
  `<select>` - the exact placement/style convention for the new admin-facing setting.
- `chat.html`'s `setSoloCloudStatus()` banner pattern and the `SOLO_CLOUD` / `"solo_cloud"`
  server-to-client flag convention - the exact pattern mirrored for a new `IS_ORG_MODE` flag and banner
  branch.
- `anthill/web/plane_routing.py`'s `planes.is_org_mode(cfg)` - already the correct predicate; no new
  detection logic needed.

## A real bug found during independent verification (fixed before landing)

The dev-council's draft invented a nonexistent `self._ensure_template(...)` helper method inside
`_RealRunpodClient.create_serverless_endpoint` - the real method builds the template inline (a `saveTemplate`
GraphQL mutation directly in the method body); calling a method that doesn't exist would have raised
`AttributeError` on every provision attempt. Fixed by editing the existing inline code in place. The
draft also referenced a nonexistent `"a40"` GPU tier key in a test (real keys are `"24"|"48"|"80"|"141"`,
VRAM-size strings) and a wrong client-side element id (`plane_banner` with an underscore; the real HTML
element is `id="plane-banner"` with a hyphen - would have raised `TypeError: Cannot set properties of
null` on exactly the code path this change adds). All three fixed before landing.

## A second, session-wide bug found only by running the FULL test suite (fixed before landing)

Adding `min_workers` to `RunpodLiveProvisioner.provision()`'s signature means EVERY call now passes
`min_workers=min_workers` through to `client.create_serverless_endpoint(...)`, regardless of whether the
caller passed it explicitly. FOUR separate fake `RunpodClient` test doubles across the test suite
(`tests/test_runpod_provision.py`, `tests/test_provision_ui.py`, `tests/test_wiki_host.py`,
`tests/test_council_provision_run.py`) do not accept a `min_workers` keyword argument at all - none of
these tests touch warm pools, but all of them broke with `TypeError: create_serverless_endpoint() got an
unexpected keyword argument 'min_workers'` until each fake was updated to accept (and default) it. Only
found by running the full suite, not just the tests this change added - a `**kwargs`-catch-all fake in
`tests/test_council_members.py` was unaffected by construction.

## Explicitly out of scope

- Time-of-day/scheduled warm-pool toggling ("warm during usage hours, scale to zero overnight," per the
  roadmap's fuller ambition) - a static, admin-set warm-worker count is this change's scope; scheduling is
  a separate, larger follow-on.
- Lambda/on-prem warm-pool settings - always-on VMs, not scale-to-zero serverless; the concept does not
  apply. RunPod only.
- Any change to team/org-plane chat behavior (correctly has no fallback - see "First correction" above).
- Reversing `one-model-per-account-solo-in-org.md`'s private-chat-inside-an-org decision - explicitly
  confirmed to stay as-is this session.
- A new interactive "use local now, or wait?" prompt for the private-chat case - it already falls back
  automatically; this change only makes that visible, it does not add a new choice.

## Acceptance criteria

1. An admin can set a warm-worker count (0 = today's behavior, N = keep N workers warm) for a RunPod-
   backed org model; `RunpodLiveProvisioner.provision()` passes it through as `workersMin` instead of the
   hardcoded `0`.
2. The setting is visible/editable only when the RunPod provider is selected, mirroring the existing
   GPU-tier field's show/hide convention.
3. A private (Solo-plane) chat inside an org account that silently falls back to the local model now
   shows a visible, non-blocking banner, matching the existing VPC-fallback banner's tone/style. A
   team/org-plane chat's existing "disabled, unreachable" behavior is unchanged. A plain Solo (non-org)
   account's chat is unaffected.
4. New/updated tests: the real GraphQL mutation carries the correct `workersMin` at every layer (Protocol,
   real client, provisioner); the Settings save round-trips and clamps the new field; the chat route
   renders the correct `IS_ORG_MODE` flag and banner condition for an org-mode Solo-plane chat, and `false`
   for a plain Solo account; every existing fake `RunpodClient` test double across the suite still works.
5. `ruff check`, `ruff format --check`, `mypy`, full test suite pass (1980 passed, 7 skipped, 0 failed).
   No em/en-dashes, no TODO/FIXME/XXX markers.
