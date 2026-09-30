# Spec: Verda (DataCrunch) Serverless Containers provisioner

Status: proposed
Lane: `pillar:platform`
Relates to: `hosting/runpod_provision.py` (the serverless template this mirrors), `hosting/provision.py`
(registry + tiers), `hosting/datacrunch_provision.py` (the existing *dedicated* Verda path - a sibling, not
this). Gives the EU-sovereign side a pay-per-use lane after Koyeb was absorbed into Mistral Compute.

## Problem

Anthill has exactly one live scale-to-zero (neocloud) serving path - RunPod (US,
`RunpodLiveProvisioner`). The EU-sovereign providers only offer **always-on** dedicated nodes: Verda's
dedicated path is built (`datacrunch_provision.DataCrunchLiveProvisioner`, `vpc` tier, a bare-VM vLLM), and
Scaleway/OVH are gated planner stubs. So an org that wants an EU-sovereign model **too big to run locally but
not busy enough to justify a 24/7 node** has no option: a dedicated 8xH200 is ~$23k/mo whether it serves one
request or a million.

Verda (formerly DataCrunch, FI) offers **Serverless Containers** - deploy your own container image from any
registry, autoscale on request volume, **scale to zero when idle**, pay only for active time. That is the
EU-sovereign equivalent of RunPod serverless, and it is the missing Lane-B (pay-per-use) EU path. (Koyeb was
the other candidate but Mistral AI acquired it in Feb 2026 - team absorbed, free tier closed - so it is off
the table as a build target.)

## Scope

**In:** a live neocloud (serverless, scale-to-zero) provisioner for Verda Serverless Containers, mirroring
`RunpodLiveProvisioner` - create a deployment serving `spec.model` over an OpenAI-compatible endpoint, poll to
registered, **guaranteed teardown on failure**, return an `OrgEndpoint`.

**Out:** the dedicated 8xH200 path (already built as `DataCrunchLiveProvisioner`, `vpc` tier); training and
the wiki host; the multi-GPU giants (a serverless cold-start reloads 400 GB+, so serverless is the ~70B lane,
not the frontier - the giants stay on the dedicated node).

## Registration: a second key in the neocloud tier

Verda spans both lanes, but the registry is one key -> one tier -> one live class (planner/live split, per
`DataCrunchPlanner`/`DataCrunchLiveProvisioner`). Keep that shape: add a **new neocloud-tier key**
`datacrunch-serverless` (name still "DataCrunch / Verda", `eu_sovereign = True`) alongside the existing `vpc`
key `datacrunch`. The tier-grouped picker then shows Verda once under always-on (Lane A) and once under
serverless / scale-to-zero (Lane B) - which is exactly the region x lane user choice. Wire it in:
`PROVIDER_KEYS`, `_TIER_OF` (-> `neocloud`), `_registry()`, and `web/provision_run.py::_LIVE_PROVIDERS`.

Alternative considered (not recommended): a single `datacrunch` key with a `lane` field on `ProvisionSpec`
selecting serverless vs dedicated. Cleaner in the picker but a bigger change (`ProvisionSpec`, tier lookup,
grouping) for no functional gain over a second key.

## Design (mirror `RunpodLiveProvisioner`)

New module `hosting/datacrunch_serverless_provision.py`:

- **Planner** `DataCrunchServerlessPlanner(_NeocloudProvisioner)` - inherits the serverless `plan()`/steps,
  `tier="neocloud"`, `serving_stack="serverless"`, `cold_start=True`, `eu_sovereign=True`.
- **Live** `DataCrunchServerlessProvisioner(DataCrunchServerlessPlanner)` - implements
  `available()`/`provision()`/`teardown()` exactly like RunPod:
  - Injectable `VerdaContainersClient` Protocol - `create_container_deployment(...)`,
    `deployment_ready(id)`, `delete_deployment(id)` - so the create -> poll-to-registered -> guaranteed
    teardown orchestration is unit-tested with a fake, no network.
  - `_RealVerdaContainersClient` - reuse the OAuth2 client-credentials auth already in
    `datacrunch_provision._RealDataCrunchClient` (`DATACRUNCH_CLIENT_ID` / `DATACRUNCH_CLIENT_SECRET`,
    base `https://api.datacrunch.io/v1`); the org already holds these creds. Talk to the Serverless
    Containers API over httpx (no heavy SDK), matching how RunPod/DataCrunch are done.
  - `provision(spec, *, client=None, validate=None, sleep=None, max_replicas=1, idle_seconds=..., name_suffix="")`:
    create a deployment running a **vLLM OpenAI-server container** (e.g. `vllm/vllm-openai`) for `spec.model`,
    with **min replicas 0** (scale-to-zero) and a **max-replica cap** (the cost guardrail); a unique
    per-attempt name (random suffix, like RunPod, to avoid leftover-name collisions); poll `deployment_ready`
    to *registered* (do NOT force a synchronous round-trip - a scale-to-zero cold start can take minutes; the
    first chat warms it); on any post-create failure, `delete_deployment` then return `ok=False` (guaranteed
    teardown). Return `OrgEndpoint(base_url=<https endpoint>, api_key=<served key>, model=spec.model)` and
    `handle=<deployment id>`.
  - `teardown(handle)` - `delete_deployment`, idempotent, never raises.

**Transport is already solved here (unlike the dedicated path):** a Verda serverless deployment is exposed
over a **Verda-managed HTTPS endpoint** (like RunPod's `https://api.runpod.ai/...`), so there is **no
cleartext-on-public-IP problem** - `llm-endpoint-secure-transport.md` / the `ANTHILL_ALLOW_INSECURE` guard do
not apply. Assert the returned `base_url` is `https://`.

## Requirements (EARS)

- The system SHALL provision a Verda Serverless Container serving `spec.model` over an OpenAI-compatible
  HTTPS endpoint, scaling to zero when idle.
- Provisioning SHALL cap cost via min-replicas 0 + a max-replica ceiling, and SHALL NOT force a synchronous
  round-trip (registration is success; the first chat warms the cold start).
- WHERE any step after deployment creation fails, the system SHALL delete the deployment and return
  `ok=False` - no billable deployment is ever left behind.
- The returned `base_url` SHALL be `https://` (Verda terminates TLS); the cleartext guard MUST NOT trigger.
- The provisioner SHALL be injectable-client testable (create/ready/delete behind a Protocol), model-free.

## Acceptance

**Unit-verifiable** (`tests/test_datacrunch_serverless_provision.py`, fake client, model-free - mirror
`tests/test_runpod_provision.py`):

- `provision` with a fake client returns `ok=True`, a `https://` `OrgEndpoint`, and a `handle`.
- A failure after creation calls `delete_deployment` and returns `ok=False` (guaranteed teardown; no leak).
- Deployment name is unique per attempt (no collision with a leftover from a failed run).
- `get_provisioner("datacrunch-serverless")` returns the live class; it is in `_LIVE_PROVIDERS`;
  `providers_for_tier("neocloud")` includes it.

**Live-only** (real Verda account; verify on first run, record it - per #254/#355; the API is unverified,
see below):

- A real deployment serves an org-plane chat turn (cold start on first request), and scales back to zero.
- Teardown removes the deployment (nothing billable remains).

## Unknowns to confirm before implementing (do not invent)

Like the `NebiusProvisioner` note, confirm these against Verda's live API/docs before wiring
`_RealVerdaContainersClient` - the existing DataCrunch client covers **instances**, not **containers**, a
different surface:

- The Serverless Containers REST endpoints (create / get-status / delete a deployment) under
  `api.datacrunch.io/v1`, and their request/response fields.
- The deployment -> served **endpoint URL** shape and how the **served API key / auth** is set.
- That an arbitrary vLLM container + a ~70B model is supported, and the cold-start time for that size.
- Billing (CONFIRMED from Verda pricing/docs, 2026-07; affects the cost-calculator assumption, not this
  provisioner's logic): charged **per active minute** a replica runs (idle = $0 true scale-to-zero; the bill
  displays 10-minute aggregates), and **cold-start minutes are billable** ("including the time spent spinning
  up or down"). Serverless carries a **~10% premium** over the dedicated rate (H100 $3.58 vs $3.25/GPU-hr;
  H200 $4.40, B200 $6.72, L40S $1.51, CPU $0.06/GPU-hr). Per-minute granularity is close to RunPod's
  per-second, not coarse - but spiky traffic on a big model pays repeated cold-start minutes.
