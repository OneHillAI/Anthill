# Spec: Model Onboarding and Sovereignty

Status: proposed
Lane: `pillar:model`
Relates to: `docs/specs/model-selection.md` (reused, not duplicated), `docs/specs/omi-ownership-mapping.md` (sister spec, owns the ownership-level data), `anthill/hosting/sizing.py`, `anthill/hosting/provision.py`, `anthill/web/app.py`, `anthill/web/templates/model_picker.html`

## 1. Introduction

`docs/specs/model-selection.md` already defines the catalog data layer: `hosting/model_catalog.json` as refreshable data, the intelligence x fit ranking in `sizing.recommend()`, and the single-GPU fit-gate `sizing.servable_on_gpu()` enforced in `POST /settings/organization`. This spec is the layer above it. It governs how a new user first encounters and chooses a compute path and model, how each option's capability and data-sovereignty properties are communicated honestly, and how compute paths map onto ownership levels. It reuses the existing ranking and fit-gate without re-deriving them, and reuses the existing admin/end-user surface split rather than building a parallel one.

The load-bearing product constraint of this spec is simplicity. A new user must be able to get to testable chat/wiki/skills by choosing between exactly two obvious paths in three to four steps. Everything else is advanced and deliberately out of the primary flow.

## 2. Requirements

### R1 - Two primary paths; one Advanced group

- THE SYSTEM SHALL present exactly TWO primary compute paths in the onboarding picker, in this order:
  1. **Local**: runs on the user's own device by downloading the model; the user just uses it that way.
  2. **Self-provisioned VPC** (Anthill-managed provisioning): the user picks one geographically divided provider from the named set and Anthill provisions and trains on it. Named providers: Lambda (US), DataCrunch/Verda (EU-sovereign, Finland), Nebius (EU), Cloud4U (Russia), RunPod (US, already live).
- THE SYSTEM SHALL present exactly ONE **Advanced** group, reachable but NOT surfaced as a primary default, folding in all of the following uniformly:
  - **Self-hosted Mac mini appliance**
  - **General on-prem**
  - **Bring-your-own AWS / Azure / GCP / IBM VPC account**
  - **Inference-provider path**: third-party API access through a vetted, EU-sovereign inference host (named examples: Berget, Infercom), pay-per-token.
- THE SYSTEM SHALL NOT present Self-hosted (Mac mini or general on-prem) as a top-level path. It exists only inside the Advanced group, because a normal user will not self-host; that is precisely why it is advanced.
- THE SYSTEM SHALL keep the inference-provider path in scope and buildable now (see R7); it SHALL NOT be marked "later, not built now."

> **NVIDIA Brev evaluated, not adopted (see #681's follow-up discussion).** Checked against Brev's own
> docs before building a `Provisioner` subclass for it: it has a real, scriptable CLI (`brev create`/
> `brev search`, GPU-type filters, fallback chains) rather than only a notebook UI, but no REST API is
> documented - the CLI is the sole programmatic interface - and no pricing is published anywhere in its
> docs. Every existing/named provider above is a direct HTTP API this codebase calls from Python with a
> transparent per-hour rate its sizing/fit-gate logic depends on; Brev would require shelling out to an
> external binary (breaking the "nothing else to install" positioning) with no cost figure to gate
> against. Not ruled out forever, just not a fit today. If wanted at all, the honest path is documenting
> it as a manual/Advanced "bring your own endpoint" case (run `brev create` yourself, point the existing
> "connect a server you already run" field at the resulting instance) - no new provisioner code.

### R2 - Capability communication (layered, honest)

- THE SYSTEM SHALL show, for each model, a percent-of-frontier bar derived from the existing `Model.intelligence` field normalised against the top catalog entry. This is pure presentation; THE SYSTEM SHALL NOT introduce any new catalog data for it.
- THE SYSTEM SHALL show, underneath the bar, a dev-stage label drawn from the ordered set: toddler -> first-grader -> high-schooler -> university -> professional/academic.
- THE SYSTEM SHALL remove parameter counts from the user-facing template (`model_picker.html`); parameter counts MAY remain only in admin/advanced surfaces.
- THE SYSTEM SHALL frame Local at the low end of the bar as "maximum ownership, capability ~30% of frontier", as a tradeoff, not as simply worse.

### R3 - Three-to-four-step process for both primary paths (testable)

- THE SYSTEM SHALL let a new user complete EITHER primary path (Local or Self-provisioned VPC) in no more than FOUR user-facing steps, counted from first landing on the picker to reaching testable chat/wiki/skills.
- THE SYSTEM SHALL enable a reviewer to literally count the steps for each primary path against the concrete step lists in the Design section (see R3 walkthrough in `## 3`) and to FAIL this requirement if either path exceeds four steps.
- THE SYSTEM SHALL NOT insert any Advanced-group configuration into the primary-path step count; Advanced options SHALL be off the primary flow.

### R4 - Onboarding-first placement, reusing the existing split

- THE SYSTEM SHALL make the compute/model tile picker the first surface a new user sees, before wiki and skills setup.
- THE SYSTEM SHALL keep advanced options reachable by reusing the existing admin/end-user split: end-user configuration via `/personalize`, admin configuration via the admin-gated settings pages (`/settings/organization`, `/settings`, `/models`, `/settings/org`, all `Depends(_require_admin)`).
- THE SYSTEM SHALL NOT build a parallel solo-vs-org settings surface.

### R5 - Fit-gate reuse (no re-derivation)

- THE SYSTEM SHALL rank and gate models using the existing `sizing.recommend()` and `sizing.servable_on_gpu()` from `docs/specs/model-selection.md`.
- THE SYSTEM SHALL NOT re-implement ranking or the single-GPU fit predicate in this layer.

### R6 - Sovereignty comparison table (requirement, not prose)

- THE SYSTEM SHALL render a sovereignty comparison table whose rows are the compute paths and whose columns are data perimeter, provider dependency, and training-vs-inference exposure, with the following values:

| Path | Data perimeter | Provider dependency | Training-vs-inference exposure |
|---|---|---|---|
| Local | Inside org's own perimeter | None | None |
| Self-hosted (Mac mini + on-prem) [Advanced] | Inside org's own perimeter | None | None |
| VPC (self-provisioned or advanced bring-your-own) | On a rented box the org controls | Infrastructure-provider dependency | Inference data on your box; no training exposure |
| Inference-provider (Advanced) | Data leaves the org's perimeter, scrubbed of structured PII first (see R9) | Model-provider dependency | No flywheel on the served model itself; successful interactions are banked locally as training data (see R7 correction below), gated by `training_eligible` |

- THE SYSTEM SHALL display these values as structured data driving the table, not as free-form copy.

### R7 - Inference-provider path: in scope, with honest tradeoff stated

> **Correction (see PR for #661 inference-provider work):** two claims below were revised after being checked against real code and current provider terms. (1) Model *origin* (e.g. Chinese vs. US vs. EU) is not the security concern - *ownership* is: a model of any origin run on your own hardware or your own cloud VPC is fine, since you own the model and the data either way. Jurisdiction of a rented VPC is a secondary concern for most orgs, not the primary rationale for the inference-provider path. (2) The "no training/knowledge flywheel" claim is now **factually wrong** - `anthill/training/collect.py`'s `record_example()` captures successful chat turns regardless of backend, and accepts `council_drafts`/`training_eligible` explicitly for this path; failed generations are excluded as specified in [`757-mlx-chat-streaming.md`](757-mlx-chat-streaming.md). The real constraint is narrower: a THIRD-PARTY CLOSED-MODEL API's own terms (OpenAI's, Anthropic's) may restrict using its outputs to train another model; a vetted open-weight host (e.g. Berget - confirmed via `ownershipindex.ai` to impose no such restriction, and to not even store prompt/output content at all) does not carry that restriction. `training_eligible` on each captured example reflects this per-source, not as a blanket "no flywheel" claim.
- THE SYSTEM SHALL implement the inference-provider path now, as an Advanced/secondary option (never one of the two primary paths from R1).
- THE SYSTEM SHALL state its rationale plainly: it trades owning the model/infrastructure (capped at Partial ownership, R8) for immediate frontier-class capability with no hardware to rent or manage, while keeping the org's wiki and banked interaction data under its own control regardless.
- THE SYSTEM SHALL mark captured interactions `training_eligible=False` when the answering path used a third-party closed-model API whose terms restrict training on its outputs, and SHALL NOT make a blanket "no flywheel is possible" claim in product copy - it is real, just gated per-source.

### R8 - Ownership-level mapping is PROVISIONAL, data-sourced

- THE SYSTEM SHALL display an ownership-level badge next to each option's capability bar, with the PROVISIONAL mapping: **Full** = Local, self-hosted Mac mini, self-hosted on-prem; **Substantial** = self-provisioned VPC, advanced bring-your-own VPC; **Partial** = inference-provider.
- THE SYSTEM SHALL source this mapping from a small data table owned by `docs/specs/omi-ownership-mapping.md`, so the values can be corrected without a code change.
- THE SYSTEM SHALL NOT hardcode the ownership mapping as inline logic in this layer, and SHALL treat it as provisional pending the sister spec's OMI-factor verification.
- THE SYSTEM SHALL NOT change these ownership levels as a consequence of the R1 primary/advanced regrouping. "Which paths are advanced to find" (a UI-placement property) and "what ownership level a path has" (a sovereignty property) are two DIFFERENT things and SHALL NOT be allowed to contradict each other. In particular: self-hosted (Mac mini + on-prem) is advanced-to-find yet remains **Full** ownership; the inference-provider path may be one keystroke away yet remains **Partial**. Placement in the UI has no bearing on ownership level and vice versa.

### R9 - Egress scrubbing and per-turn audit trail (#661 follow-up)

- THE SYSTEM SHALL redact structured PII (email, phone, SSN, payment cards, API keys/JWTs, IBANs) from every message before it crosses the network to a remote OpenAI-compatible endpoint - an org's own RunPod/onprem server, or a third-party inference provider - and SHALL restore the original values in the reply so the user never sees a raw placeholder token. See `docs/specs/661-egress-privacy-and-audit.md` for the full requirement set (implemented in `anthill/inference/openai_compat.py`).
- THE SYSTEM SHALL NOT scrub calls to a loopback/on-device endpoint (e.g. the local mlx-lm fine-tune server) - nothing crosses a perimeter there, and scrubbing would only add latency for no privacy benefit.
- THE SYSTEM SHALL record which model and endpoint answered every chat/task/agent turn in the existing admin audit log (`anthill.web.audit.log_inference_call`), and SHALL NOT record the message content itself there - the egress scrubber is the content-safety control, the audit log is the who/when/which-model trail.
- THE SYSTEM SHALL show the user, per successful model response, whether that turn was answered locally
  or left the device - not only logged for later admin review. Failed-generation badge filtering is
  owned by [`757-mlx-chat-streaming.md`](757-mlx-chat-streaming.md).

## 3. Design

- **Template.** Replace the plain radio list in `anthill/web/templates/model_picker.html` with a tile UI: two prominent primary tiles (Local, Self-provisioned VPC) and a collapsed/secondary "Advanced" affordance opening the four Advanced options (Mac mini, general on-prem, bring-your-own AWS/Azure/GCP/IBM VPC, inference-provider).
- **View model.** Extend `_model_picker_view` and the `GET/POST /setup/model` route in `anthill/web/app.py`.
- **Onboarding order.** The `/setup/model` picker is placed first in the new-user flow, before wiki and skills.
- **Concrete 3-4 steps for the primary paths (this is the literal list R3 is tested against):**
  - **Local path (3 steps):**
    1. Land on `/setup/model`, select the **Local** tile.
    2. Pick a model from the fit-gated list (`sizing.recommend()` filtered by `sizing.servable_on_gpu()`); model downloads.
    3. Land in testable chat/wiki/skills.
  - **Self-provisioned VPC path (4 steps):**
    1. Land on `/setup/model`, select the **Self-provisioned VPC** tile.
    2. Pick one geographically divided provider (Lambda / DataCrunch-Verda / Nebius / Cloud4U / RunPod).
    3. Pick a model from the fit-gated list; Anthill provisions and begins training on the chosen provider.
    4. Land in testable chat/wiki/skills.
  - Advanced-group configuration is NOT part of either count and is reached only via the separate Advanced affordance / existing settings pages.
- **Providers.** Add DataCrunch/Verda, Nebius, Cloud4U as subclasses of the `Provisioner` Protocol in `anthill/hosting/provision.py`, with registry entries. Bring-your-own AWS/Azure/GCP/IBM remain Advanced and are not implemented live here (out of scope). RunPod and Lambda already exist.
- **Inference-provider.** Implement a `Provisioner`-adjacent inference-provider adapter (vetted EU-sovereign hosts, e.g. Berget/Infercom-style), surfaced only in the Advanced group, badged **Partial**, with the R7 rationale/cost copy driven from structured data.
- **OMI data source.** The ownership mapping is read from the sister spec's data table via a small loader; placement (primary vs advanced) is a separate template concern and never derived from the ownership badge.

## 4. Tasks

- [ ] Add the OMI mapping loader consuming the sister spec's data table.
- [ ] Extend `_model_picker_view` in `anthill/web/app.py` to expose two primary tiles + one Advanced group.
- [ ] Rewrite `anthill/web/templates/model_picker.html` as a tile UI; remove parameter counts; two primary tiles, collapsed Advanced group.
- [ ] Wire `/setup/model` as the first onboarding step.
- [ ] Route advanced/edit into existing settings pages; add no new settings surface.
- [ ] Add DataCrunch/Verda, Nebius, Cloud4U provider subclasses + registry entries.
- [ ] Implement the inference-provider path now (adapter + Advanced-group tile + Partial badge + R7 rationale/cost copy from structured data) rather than deferring it.
- [ ] Confirm ranking/fit-gate calls delegate to `sizing.py` (no re-derivation).
- [ ] **Validation - step-count walkthrough:** Before implementation is considered complete, perform a literal step-count walkthrough of BOTH primary paths (Local, Self-provisioned VPC) against R3's concrete step lists; fail the change if either path exceeds four user-facing steps.
- [ ] **Validation - live provisioning, Lambda:** Real, live, end-to-end provisioning test (not code review). Has live-validated history today.
- [ ] **Validation - live provisioning, RunPod:** Real, live, end-to-end provisioning test (not code review). Has live-validated history today.
- [ ] **Validation - live provisioning, DataCrunch/Verda:** Real, live, end-to-end provisioning test. UNPROVEN GAP - no live-validated history today; must be closed, not assumed.
- [ ] **Validation - live provisioning, Nebius:** Real, live, end-to-end provisioning test. UNPROVEN GAP - no live-validated history today; must be closed, not assumed.
- [ ] **Validation - live provisioning, Cloud4U:** Real, live, end-to-end provisioning test. UNPROVEN GAP - no live-validated history today; must be closed, not assumed.
- [ ] Cross-link the sister specs.

## 5. Out of scope

- The catalog data layer, ranking algorithm, and single-GPU fit-gate (owned by `docs/specs/model-selection.md`).
- The inference-provider path's per-member config shape (belongs to the product-council-architecture spec); the path itself IS in scope here.
- Final OMI-factor justification and canonical ownership values (owned by `docs/specs/omi-ownership-mapping.md`).
- Live implementation of the Advanced bring-your-own AWS/GCP/Azure/IBM providers.
- Any new admin-vs-end-user settings surface.
