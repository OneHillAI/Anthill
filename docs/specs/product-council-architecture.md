# Spec: Product Council Architecture

Status: proposed
Lane: `pillar:model`
Relates to: `anthill/hosting/provision.py`, `anthill/web/db.py` (`OrgSettings`), `anthill/wiki/ask.py`, `anthill/web/scheduler.py`, `anthill/web/agents_run.py`, `anthill/verify/verify.py`, `docs/specs/model-selection.md` (`sizing.servable_on_gpu()`), the ASDD kit's `dev_council` config pattern (conceptual reuse, not a code dep)

## 1. Introduction

Anthill today locks each account to a single inference model. `OrgSettings` (`anthill/web/db.py`) carries one (endpoint, model, key) worth of state per `org_id`, read once per chat turn via `_cfg` / `_backend_from_cfg`, and each `Provisioner.provision()` stands up exactly one endpoint serving one model.

This spec supersedes "one model per account" with one council configuration per account: each account still chooses exactly one thing, but that thing is now an ordered list of council members (2-3 typical), orchestrated with Mixture-of-Agents (MoA) to produce a single synthesised answer. The account-uniqueness lock, the Provisioner Protocol, and the single-endpoint provisioning primitive are all unchanged; the change lives one layer up in the account-config and save/provision loop, plus an additive council answer path applied uniformly across the three surfaces where Anthill answers on the user's behalf. It draws its config shape from Anthill's own proven ASDD dev-council tooling. The existing verify.py cross-check is left untouched and cross-referenced.

Council-everywhere is the key differentiator: the council must answer in a council way not only for chat, but for scheduled tasks and agent runs too.

## 2. Requirements

### R1 - One council configuration per account
- THE SYSTEM SHALL store, per account, exactly one council configuration instead of one model.
- THE SYSTEM SHALL keep `OrgSettings.org_id` as `unique=True`.
- THE SYSTEM SHALL represent the account's members as an ordered list of (endpoint, model, key) tuples.
- THE SYSTEM SHALL continue to support a person running more than one account under the same installed application; multi-account creation is existing capability requiring no new work here.

### R2 - No Provisioner Protocol change; per-member provisioning in the config layer
- THE SYSTEM SHALL NOT modify the Provisioner Protocol; plan/provision/teardown remain unchanged.
- THE SYSTEM SHALL call the existing single-endpoint provision() once per council member.
- THE SYSTEM SHALL confine council behaviour to the /settings/organization save/provision/teardown flow.

### R3 - Per-member / shared-provider config shape (ASDD pattern reuse)
- THE SYSTEM SHALL store each member's provider endpoint, model id, and API key or runtime token.
- THE SYSTEM SHALL permit a single shared endpoint/credential when all members share one multi-model host.
- THE SYSTEM SHALL reuse the ASDD kit's dev_council per-member-first-then-shared-fallback shape.
- THE SYSTEM SHALL support two member lifecycle kinds: Anthill-provisioned (VPC) and referenced-only (inference-provider, never provisioned or torn down by Anthill).
- THE SYSTEM SHALL build and ship the referenced-only inference-provider member kind as part of THIS spec's tasks; it is in scope now, not deferred. The rationale: routing access to Chinese-origin models through a vetted host (e.g. Berget, Infercom) is a stronger security posture than raw self-hosting, at the explicit cost of no training/knowledge benefit through that path (the model is used, not learned from).

### R4 - Mixture-of-Agents orchestration across all three answer surfaces
- THE SYSTEM SHALL orchestrate the council using Mixture-of-Agents (MoA): members within a layer run IN PARALLEL, a layer's outputs feed the next layer as context, ending in synthesis.
- THE SYSTEM SHALL NOT implement the council as a naive sequential N-model chain.
- THE SYSTEM SHALL apply the council answer path to all THREE surfaces, each named explicitly:
  - `anthill/wiki/ask.py` - chat answers.
  - `anthill/web/scheduler.py` - scheduled tasks.
  - `anthill/web/agents_run.py` - agent runs.
- THE SYSTEM SHALL treat these three as the committed council scope because they are precisely the same three call sites `anthill/verify/verify.py`'s existing cross-check already touches; that established precedent defines the surface set here.

### R5 - Council total-footprint fit-gate (sum, not largest member)
- THE SYSTEM SHALL validate that the council's TOTAL resource footprint - the SUM of every member's GPU, RAM, and storage requirements - fits, not the footprint of the largest single member alone.
- THE SYSTEM SHALL extend the existing single-GPU fit-gate `sizing.servable_on_gpu()` (from `docs/specs/model-selection.md`) so that a local council of 2-3 smaller models sharing the RAM/VRAM budget a single larger model would have used is validated against that shared budget as a sum.
- THE SYSTEM SHALL reject a local/self-hosted council configuration whose summed member footprint exceeds the fit-gate, rather than admitting it on a per-member basis.

### R6 - SGLang as a serving-engine candidate (local/self-hosted only)
- THE SYSTEM SHALL name SGLang as a candidate serving engine for the local/self-hosted council path.
- THE SYSTEM SHALL treat SGLang as a serving-layer concern distinct from MoA orchestration, a candidate needing validation, not a committed dependency.

### R7 - Org account scope: shared-only, no in-account fallback
- THE SYSTEM SHALL define an org account as a shared account setting: same council, same knowledge base, same conditions, for everyone in it.
- THE SYSTEM SHALL NOT provide any in-account personal/local council mode, including as an offline fallback.
- THE SYSTEM SHALL direct a person wanting personal compute to a separate individual account.

### R8 - verify.py relationship
- THE SYSTEM SHALL leave verify.py's sequential cross-check unchanged, neither deleted nor superseded here.
- THE SYSTEM SHALL treat folding verify into the council path as a future follow-up, out of scope here.

## 3. Design

- **Schema.** `OrgSettings` migrates from a single (endpoint, model, key) triple to an ordered member list, each member carrying endpoint, model id, credential, and a lifecycle kind (`vpc` | `inference-provider`), following the ASDD `dev_council` per-member-first-then-shared-fallback shape.
- **Config/save loop.** `/settings/organization` loops the member list, calling the unchanged single-endpoint `provision()` once per `vpc` member and skipping provision/teardown for `inference-provider` members (referenced-only). Before save, the loop runs the R5 fit-gate over the SUMMED footprint of the council members.
- **Fit-gate.** Extend `sizing.servable_on_gpu()` (`docs/specs/model-selection.md`) to accept the council member set and validate the sum of their GPU/RAM/storage against the single-GPU budget; the largest-member-only check is insufficient and not used.
- **Orchestration - chat (`anthill/wiki/ask.py`).** Additive, opt-in MoA council path producing one synthesised chat answer.
- **Orchestration - scheduled tasks (`anthill/web/scheduler.py`).** The same MoA council path drives scheduled-task answers, matching an existing verify.py call site.
- **Orchestration - agent runs (`anthill/web/agents_run.py`).** The same MoA council path drives agent-run answers, matching an existing verify.py call site. These three are the same three files verify.py's cross-check already touches.
- **Inference-provider member type.** Built now: config carries a vetted host's endpoint/model/token; no provisioning, no teardown. Security rationale stated in R3.
- **Serving.** SGLang is a candidate serving engine for the local/self-hosted path only, pending the validation spike.
- **verify.** Cross-referenced as unchanged; folding it into the council is a later follow-up.

## 4. Tasks
- [ ] Migrate OrgSettings to an ordered member list (with per-member lifecycle kind).
- [ ] Update _cfg / _backend_from_cfg to resolve the member list.
- [ ] Update /settings/organization to loop members for provision/teardown.
- [ ] Extend `sizing.servable_on_gpu()` to validate the SUMMED total footprint of the council (GPU/RAM/storage), and wire it into the save/provision loop as a pre-save gate.
- [ ] Add DataCrunch/Nebius/Cloud4U registry entries, no Protocol change.
- [ ] Implement the additive, opt-in MoA council path in `anthill/wiki/ask.py` (chat).
- [ ] Implement the MoA council path in `anthill/web/scheduler.py` (scheduled tasks).
- [ ] Implement the MoA council path in `anthill/web/agents_run.py` (agent runs).
- [ ] Build and test the referenced-only inference-provider member type now (config, resolution, no provision/teardown), including at least one vetted host (Berget/Infercom) end-to-end.
- [ ] Empirical council-vs-single-model comparison test on a real question set, run across at least the local council and the self-provisioned-VPC council configurations, measuring wall-clock latency and answer-quality difference - not an architectural claim alone.
- [ ] Technical-validation spike for SGLang.
- [ ] Cross-reference verify.py as unchanged.

## 5. Out of scope
- Any change to the Provisioner Protocol.
- Folding verify.py into the council path.
- Any in-account personal/local fallback mode.
- New multi-account creation flow (already exists).
- Committing to SGLang before validation.
- Making the council path the default before it is proven.
