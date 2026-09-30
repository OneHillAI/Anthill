# Spec: a Solo account can train on its connected cloud GPU, no organisation required

Status: implemented
Lane: `pillar:model`
Relates to: `docs/RUNPOD_LIVE_RUN_RUNBOOK.md` (the launch-critical live run this unblocks for a Solo
account, issue #254), `docs/specs/expert-tier-compound-compute-and-escalation.md` (the "Your cloud"
attachment this reuses), `anthill/training/backends/__init__.py` (the registry this now reaches
from Solo too).

## 1. Introduction

Founder report: "I don't know where I can connect RunPod. I run this on my local machine, and in
settings I see nothing where I can change it except... your cloud, but I don't want to do anything
in the cloud [for daily use]... if you run a Solo account, you can train your data, but this needs
to happen on RunPod... you don't need to create an organization for that."

Tracing the code confirmed the account was right and the product was wrong. Two separate gaps, not
one:

1. **`is_local_training(cfg)`** (`training/model_select.py`) gated purely on
   `deployment_topology == "solo"` - it never looked at `solo_compute`. A Solo account could never
   reach cloud training no matter what it connected: `_training_readiness` would always render the
   on-device "install mlx-lm" branch, and the Settings -> Model -> Advanced "Self-tuning" card's
   "Train now" button stayed disabled with no way to attach a backend from there - exactly the
   "I cannot connect or do anything about it" the founder hit.
2. **Solo's own compute chooser never derived `training_backend`/`training_provider`.** The
   machinery to do this already existed and already worked - `settings_organization_backend` (the
   *admin-only*, org-topology `/settings/organization` route) already calls
   `training_backend_for_provider(lead["provider"])` when an org picks its serving cloud. Solo's
   own, simpler "Your cloud" flow (`_apply_solo_compute`, `POST /personalize/compute` - the one the
   founder actually found and used) set the identical underlying fields
   (`org_provider`/`org_provision_key_enc`) but never called that same derivation, so even
   switching to "Your cloud" left `training_backend` empty.

Both gaps trace back to one conflation: "solo" was treated as synonymous with "trains on-device."
They are two different axes - who trains (topology: personal gold vs org-scope gold) and where
compute runs (on-device vs a connected cloud GPU) - and only the org path ever exercised the second
axis independently of the first.

Also fixed in passing, found while tracing the readiness display end to end: `/training`'s gold
count always queried `scope == "org"` regardless of topology, while `/training/run`'s actual gold
check already correctly used `scope == "personal"` for Solo - so the readiness page could show
"0 gold" for a Solo account that in fact had personal gold to train on, or vice versa. Brought the
display in line with what a run actually checks.

## 2. Requirements

### R1 - Solo + a connected cloud provider trains on that provider, not on-device
- WHEN a Solo account's compute tier is a connected cloud provider (`solo_compute == "cloud"`),
  THE SYSTEM SHALL derive `training_backend`/`training_provider` from that provider via the same
  `training_backend_for_provider` mapping an org's serving-cloud choice already uses, at the same
  moment `_apply_solo_compute` saves the "Your cloud" choice.
- THE SYSTEM SHALL make `_training_readiness` and the training dispatch (`get_backend`,
  `_is_local_mlx`) treat this account as NOT local training - it must never fall into the
  on-device "install mlx-lm" branch once a cloud provider is connected.

### R2 - Switching back to "Your machine" clears the stale cloud backend
- WHEN a Solo account's compute tier changes back to local, THE SYSTEM SHALL re-derive
  `training_backend`/`training_provider` to the on-device default, the same way `settings_
  organization_backend` already unconditionally re-derives on every save - a stale
  `training_backend="endpoint"` left over from a prior cloud choice must never let a scheduled or
  manual run dispatch to a since-abandoned (or since-revoked) cloud account, nor mismatch the
  MLX-specific serve path against a non-MLX-trained adapter.

### R3 - Gold scope stays topology-based, independent of R1/R2
- THE SYSTEM SHALL keep gold-scope selection (personal vs org-scope) keyed on topology alone
  (a new `is_solo_account`), never on where compute runs - a Solo account is one user whether it
  trains on-device or on a rented GPU, and has no org-scope gold either way.
- THE SYSTEM SHALL make `/training`'s displayed gold count match what `/training/run` actually
  checks (both now `is_solo_account`-keyed), closing the pre-existing display/reality mismatch
  found while implementing R1.

### R4 - The account never has to "become an org" for any of this
- THE SYSTEM SHALL NOT require `deployment_topology` to change for a Solo account to use cloud
  training - `/training`'s own copy and links (which previously said "org cloud" and linked to the
  admin-only `/settings/organization`) SHALL branch on topology so a Solo account is pointed back
  at its own `/personalize#model`, never at a page meant for an org admin.
- THE SYSTEM SHALL surface a working entry point from Settings -> Model -> Advanced for a
  Solo-cloud account (which doesn't get the on-device Self-tuning card) rather than showing nothing
  - a small card linking to `/training`, the same authoritative page an org's cloud training
  already lives on.

## 3. Design

- **`training/model_select.py`**: split into `is_solo_account` (topology only - gold scope,
  Self-tuning card applicability) and `is_local_training` (topology AND `solo_compute != "cloud"` -
  which backend actually fires). `training_on` now keys off the narrower `is_local_training`, so
  Solo-cloud training needs the explicit `training_enabled` toggle exactly like org-cloud does
  (it costs real money either way) while genuinely free on-device Solo stays always-on.
- **`training/executor.py`**: `_gold`'s scope split moved to `is_solo_account` (R3); `_is_local_mlx`
  needed no change - it was already correct once `is_local_training` narrowed under it.
- **`_apply_solo_compute`'s cloud branch** (`anthill/web/app.py`): after setting `org_provider`/
  `org_provision_key_enc` (unchanged), now also calls `training_backend_for_provider` and sets
  `training_backend`/`training_provider`/`training_base_model`, mirroring `settings_organization_
  backend`'s existing block line for line (R1).
- **`_apply_solo_compute`'s local branch**: unconditionally re-derives
  `training_backend_for_provider("onprem")` before branching on council/single-model, the same
  "always re-derive on this save" pattern the org route already relies on (R2).
- **`training_page`**: gold-count query now scope-matches `is_solo_account` (R3); passes `solo`
  into the template context (same key name `settings_organization_backend` already uses for its own
  template, so the shared `_org_cloud_tabs.html` partial works from either includer).
- **`training.html`**: "Connect your org cloud" / "org cloud" / "org model" copy and links now
  branch on `solo` to point at `/personalize#model` "Change where it runs" instead (R4).
- **`_org_cloud_tabs.html`**: for `solo`, shows only the "Training" tab - the "Model"/"Wiki" tabs
  are the admin-only org backend pages and dead-end a Solo account (R4).
- **`personalize.html`**: new `can_tune_cloud` (Solo + not local training) renders a small card
  under Model -> Advanced linking to `/training`, instead of Solo-cloud getting no self-tuning
  entry point at all (R4). `can_tune` (the full on-device card) is unchanged.
- **Also fixed, same investigation**: the "Model storage" row moved from Model -> Advanced to
  This device (a separate, smaller UX fix reported alongside this one - disk usage is a device
  fact, and the move also removes one layer of nesting to reach `/models`).

## 4. Tasks

- [x] `is_solo_account`/`is_local_training` split; `training_on` keyed on the narrower one (R1, R3).
- [x] `_apply_solo_compute`'s cloud branch derives `training_backend`/`training_provider`/
  `training_base_model` (R1).
- [x] `_apply_solo_compute`'s local branch resets them (R2).
- [x] `executor.py`'s gold-scope split moved to `is_solo_account` (R3).
- [x] `training_page`'s gold-count query fixed to match `/training/run`'s actual scope (R3).
- [x] `training.html` / `_org_cloud_tabs.html` branch on `solo` instead of always assuming org (R4).
- [x] `personalize.html`'s `can_tune_cloud` card (R4).
- [x] Tests: connecting RunPod under Solo's "Your cloud" wires training end to end; the readiness
  page reflects it and never shows admin-only org links/copy for Solo; switching back to "Your
  machine" resets the stale cloud backend; Solo-cloud training needs the explicit enable toggle;
  the Self-tuning card links to `/training` for Solo-cloud; the pre-existing gold-scope test fixture
  fixed to match reality. Verified against a revert (6 new integration tests fail without the
  change - one as an ImportError, proving the split is load-bearing - and pass with it).

## 5. Out of scope

- **Actually running the RunPod live validation itself** (issue #254) - this spec only removes the
  product-level blocker; the live run remains a deliberate, owner-performed action per its own
  runbook.
- **`/models`'s own internal structure** - only its entry point moved; the page itself wasn't
  audited for further nesting.
- **Re-architecting `_org_cloud_tabs.html`/`/settings/organization` more broadly for Solo** - this
  spec hides the two tabs that dead-end Solo; it doesn't attempt a fuller Solo-aware redesign of the
  org backend page itself.
