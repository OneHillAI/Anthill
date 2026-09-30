# Spec: per-provider GPU availability (static map + weekly drift check)

Status: proposed
Lane: `pillar:platform`
Relates to: the Cloud & model picker (`settings_organization.html`, `POST /settings/organization`) and
`sizing.servable_on_one_gpu` (#637). This adds the *provider availability* axis the fit gate does not cover.

## Problem

The picker sizes a model against a GPU with `servable_on_one_gpu`, but it treats the GPU list as
**provider-agnostic** - the template says "the list is the same whichever provider you pick." That is not
true. Provider research (July 2026) found the big GPUs are **quota-gated** on the EU-sovereign clouds:

- **Scaleway (EU):** L4 (24GB) and L40S (48GB) are self-serve; **every H100 / H200 tier is gated behind a
  "contact Support" quota request** (verified in Scaleway's own org-quotas table).
- **OVHcloud (EU):** all large GPU quota raises go through a **manual support ticket**.
- **Lambda (US):** H100 (incl. an 8x H100 node) is **genuinely self-serve** on-demand.
- **RunPod (US):** single GPUs up to B300 are self-serve.

So the picker currently implies an admin can one-click an 80GB H100 on Scaleway, which they cannot. For a
sovereignty-first product the honest per-provider availability *is* the decision.

## Approach: static, refreshed weekly, never real-time

No live provider API polling on page load (quota policy changes on the order of months, not seconds). Two
small pieces:

### 1. A static availability map (source of truth)

A curated table, `provider_gpu_availability`, keyed by `(provider_key, gpu_tier_key)` returning one of:

- `self_serve` - launchable with just account + card + API key.
- `quota_gated` - listed, but needs a one-time support/quota unlock first.
- `unavailable` - the provider does not offer that tier.
- `unverified` - not confirmed by research; shown as such, never guessed.

Each entry carries a `source_url` and a `verified_on` date. Seed it only with the **research-verified**
facts above; mark everything else `unverified` rather than inventing values. Lives next to the provider
registry (`hosting/`), consumed by the GET handler.

### 2. Picker reads it statically (inform, do not block)

When a provider is selected, each GPU option is labelled from the map: "self-serve", "needs a quota unlock
on <provider>", or "availability unverified". Gated tiers stay **selectable** - a quota unlock is a real
thing the customer can do - so this is an honesty signal, not a new hard gate. The existing
`servable_on_one_gpu` `too_big` gate is unchanged. No change to the save path or persisted columns.

### 3. Weekly drift check (keeps the static map honest)

A scheduled job - weekly, in the style of the repo's existing doc-audit agent (report drift, never edit
unattended) - re-fetches each `source_url` and checks whether the key claim still holds (e.g. Scaleway's
H100 row still says "contact Support"). On a mismatch it **reports** (log / notification / an issue) for a
human to update the map and its `verified_on`. It MUST NOT auto-edit the map. Cadence can reuse the
existing scheduler tick or a cron routine; the check itself is a handful of HTTP GETs + string checks.

## Acceptance

- With Scaleway selected, the 80GB (H100) and 141GB (H200) options are labelled "needs a quota unlock",
  while 24GB/48GB are "self-serve"; with Lambda selected, 80GB is "self-serve". (Unit test the map +
  the label mapping, model-free.)
- Gated tiers remain selectable and the save path is unchanged (no new refusal).
- The map exposes `source_url` + `verified_on` per entry; unresearched combos read `unverified`, never a
  fabricated value.
- The weekly check reports a drift (a changed provider page) without editing the map; a matching page
  produces no report. Test with an injected fetcher, live check confirmed on a first run.

## Out of scope

- Real-time provider API polling / live quota introspection.
- Hard-blocking a gated selection (quota is unlockable - inform, don't forbid).
- APAC providers and EU neocloud alternatives (DataCrunch/Nebius/Genesis) - no verified data yet; they
  enter the map as `unverified` until a follow-up research run fills them.
- The multi-GPU / frontier tiers (separate: [`multi-gpu-tensor-parallel-serving.md`](multi-gpu-tensor-parallel-serving.md)).
