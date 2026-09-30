# Spec: OMI Ownership Mapping

Status: proposed
Lane: `pillar:model`
Relates to: `docs/specs/model-catalog-trust.md` (reuse its trust shape), `docs/specs/model-onboarding-and-sovereignty.md` (the sovereignty table this feeds), the AI Ownership Index (ownershipindex.ai, repo OneHillAI/aoi)

## 1. Introduction

The sovereignty table in `docs/specs/model-onboarding-and-sovereignty.md` needs a defensible, non-invented way to describe how "owned" a given compute path is. Rather than mint an Anthill-only taxonomy that would fragment vocabulary against a live, public product, this spec reuses the AI Ownership Index (OMI) methodology directly and maps each Anthill compute path onto an OMI ownership level.

OMI's methodology (canonical source: `OneHillAI/aoi`, `methodology/ownership.md`) defines **five levels** and **four floor-weighted factors**:

- **Levels:** Full (all four factors strong), Substantial (strong on use-&-modify and data-control, no factor weak), Partial (exactly one factor weak, or use-&-modify/data-control only moderate), Limited (two factors weak), None (three or more weak, or a closed black box).
- **Factors (floor-weighted, the *weakest* caps the whole, a conjunction and not an average):** (1) use-&-modify (license/gating/trainability), (2) transparency (openness of weights/docs/training data), (3) reliability (operational + safety scores), (4) data-control.
- **Data-control definition (literal):** strong = "self-hosted models not phoning home; OR providers contractually avoiding input training with zero-retention defaults"; weak = "providers training/retaining inputs by default; self-hosted models phoning home." Per the framework's own words, data-control is about model/provider **behaviour**, not who owns the physical hardware, a well-run VPC deployment of your own open model, phoning home to nobody, can score the same strong data-control as pure on-prem.

This spec ships the **mechanism** (data table, trust posture, badge sourcing) now. The **specific mapping values** and their rationale are marked provisional pending an explicit founder-deferred verification (see R4).

## 2. Requirements

### R1 - Reuse OMI methodology and vocabulary
- THE SYSTEM SHALL express per-compute-path ownership using OMI's five levels (Full, Substantial, Partial, Limited, None) and four floor-weighted factors (use-&-modify, transparency, reliability, data-control), and SHALL NOT define a second, Anthill-only ownership taxonomy or level naming alongside the live OMI product.
- THE SYSTEM SHALL treat floor-weighting as a conjunction (weakest factor caps the level), matching the canonical `OneHillAI/aoi` `methodology/ownership.md`, and SHALL NOT compute an average.

### R2 - Provisional default mapping (compute path -> OMI level)
- THE SYSTEM SHALL show, as the default per-compute-path ownership level, the following mapping:
  - **Full**: Local; self-hosted Mac mini; self-hosted on-prem.
  - **Substantial**: self-provisioned VPC; advanced bring-your-own VPC.
  - **Partial**: inference-provider (later).
- THE SYSTEM SHALL record the *working* (dev-council round 2) rationale for VPC scoring below self-hosted despite data-control alone not distinguishing them: an infrastructure-provider dependency plausibly moderates the **reliability** factor's operational sub-score (availability/continuity/account-standing risk), which under floor-weighting can cap an otherwise-strong profile from Full to Substantial.
- THE SYSTEM SHALL label this mapping and its rationale as **provisional** at the point of presentation, subject to R4.

### R4 - VPC-downgrade rationale is UNVERIFIED and deferred (do not present as settled)
> **⚠ UNVERIFIED: DEFERRED VERIFICATION REQUIRED.** The rationale in R2 (that operational-reliability is the OMI-faithful basis for the VPC downgrade) has **not** been independently verified against how OMI's "operational" sub-score is actually defined and applied. OMI's operational sub-score concerns a *model's own documented deployability*, not necessarily a *specific deployment's* infrastructure-provider dependency; these may not be the same thing. This rationale was proposed during design discussion only. The founder has explicitly deferred this check ("check on the OMI scoring later").

- THE SYSTEM SHALL present the R2 mapping table and its rationale as provisional pending the founder's deferred verification, and SHALL NOT present them as a settled, verified reading of OMI's scoring criteria.
- THE SYSTEM SHALL keep this caveat visible wherever the mapping rationale is surfaced (spec and, where feasible, the data file), so the deferral is not lost.

### R5 - Mapping is correctable data, not inline logic
- THE SYSTEM SHALL store the compute-path -> OMI-level mapping as **data** (a small table/config file), separate from application logic.
- THE SYSTEM SHALL allow the mapping values and their rationale text to be corrected, including as a result of R4's verification, **without a code change**.
- Because the mapping is correctable data, the R4 caveat SHALL NOT block shipping the mechanism defined here.

### R6 - Live OMI data reuses the model catalog trust posture, fails closed
- THE SYSTEM SHALL, for any live OMI per-model/per-provider data consumed for a badge in the picker (see `docs/specs/model-onboarding-and-sovereignty.md`), reuse the exact trust posture defined in `docs/specs/model-catalog-trust.md`: HTTPS-only, redirects refused, body size capped, identity fields validated, wholesale refusal if any row is unsafe, Sigstore keyless signing (GitHub OIDC -> Fulcio -> Rekor), and fail-closed to the last-known-good cached copy on any verification failure.
- THE SYSTEM SHALL, on fetch or verification failure, fail **closed** to the conservative level implied by the static compute-path mapping (R2), and SHALL NEVER fail open to "Full."

## 3. Design

- **Data file.** A small static table `data/omi-ownership-mapping.<ext>` maps each compute path to a provisional OMI level, with a `rationale` field and a machine-readable `verified: false` flag plus a `deferred_note` carrying the R4 caveat. The sovereignty table renders directly from this file.
- **No new runtime code.** The mechanism is presentation over static, correctable data; the default badge is a lookup into the data file.
- **Live OMI badge (optional enrichment).** When a live OMI datum is available for a specific model/provider, it is fetched and verified through the shared catalog-trust client (`docs/specs/model-catalog-trust.md`). Only signed, verified rows may upgrade or annotate a badge. Any failure falls back to the static compute-path level, never above it, never to Full by default.
- **Level source of truth.** The five level names and four factor names are taken verbatim from `OneHillAI/aoi` `methodology/ownership.md`; they are not redefined here.

## 4. Tasks
- [ ] Add `data/omi-ownership-mapping.<ext>` with the R2 rows, provisional rationale, `verified: false`, and the R4 deferred note.
- [ ] Wire the sovereignty table in `docs/specs/model-onboarding-and-sovereignty.md` to render from that data file.
- [ ] Route any live OMI badge fetch through the existing catalog-trust client (`docs/specs/model-catalog-trust.md`); assert fail-closed to the static level in tests.
- [ ] Add a test asserting that no failure path can yield "Full" from live OMI data alone.
- [ ] Track a follow-up item for the founder's deferred OMI-scoring verification (R4); on resolution, correct the data file only (no code change).

## 5. Out of scope
- Final verification of the VPC-downgrade rationale against OMI's actual operational sub-score (deferred to founder; see R4).
- Any Anthill-specific ownership taxonomy or alternate level names (explicitly excluded by R1).
- The inference-provider path's detailed treatment beyond the provisional "Partial (later)" placeholder.
- Changes to `anthill/` runtime code (none required to ship this mechanism).
