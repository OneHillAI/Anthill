# Spec: distributed local pooling (#661 Tier 5)

Status: implemented. Lane: `pillar:model`.
Relates to: `docs/specs/local-vs-frontier-capability-roadmap.md` (Tier 5 section - the originating
roadmap, corrected in place by this change), `docs/specs/product-council-architecture.md` (the
on-prem provider path this reuses).

## 1. Problem

`docs/specs/local-vs-frontier-capability-roadmap.md`'s Tier 5 proposed pooling 2-4 of an org's own LAN
machines (via llama.cpp's `rpc-server`) to push the local serving ceiling from ~70B to roughly ~150B
params - a moderate ceiling lift for orgs that already own several boxes, explicitly not a path to
1T-class models.

Two of that roadmap section's claims were checked against real code and found wrong before building
anything: it says Tier 5 should reuse "the mesh's mTLS/trust layer" (`anthill/mesh_auth.py`) for node
discovery. `mesh_auth.py` is actually a shared-secret bearer token (there are no certificates anywhere
in this codebase), and even corrected to "bearer token," it does not apply - `require_mesh()`/
`mesh_headers()` protect HTTP endpoints, and llama.cpp's `rpc-server` protocol is not HTTP. Separately,
since llama-server started with `--rpc host:port,...` still exposes the identical OpenAI-compatible
`/v1` API to a client, no new inference backend class is needed either - the existing "connect a
server you already run" path already handles the pool's actual chat traffic once pointed at the main
node.

## 2. Requirements

- THE SYSTEM SHALL let an on-prem org admin describe a pool of LAN machines they have already set up
  themselves: the main node's memory + kind, and up to 3 additional worker machines (label, host,
  port, memory each). THE SYSTEM SHALL NOT launch, SSH into, or otherwise manage any `rpc-server` or
  `llama-server` process on any machine.
- THE SYSTEM SHALL require `org_provider == "onprem"` to enable cluster pooling, enforced server-side
  (not only hidden client-side). Saving a non-onprem provider on the main settings form SHALL clear
  `org_cluster_enabled` if it was previously on.
- THE SYSTEM SHALL estimate the pool's aggregate capacity from self-reported node memory, using a
  discount factor for network overhead that is explicitly labeled as an unvalidated placeholder, not a
  measured benchmark - the estimate note SHALL say so in the rendered UI, not only in code comments.
- THE SYSTEM SHALL warn, and SHALL NOT block, saving settings when the org's currently-selected model
  exceeds the cluster capacity estimate - the estimate rests on unmeasured numbers, unlike the cloud-
  GPU fit gate (`sizing.servable_on_gpu`), which blocks because real money is about to be spent
  provisioning something that would then fail to load.
- THE SYSTEM SHALL check each configured worker's reachability via a raw TCP connection attempt only,
  computed fresh on every page load (never persisted), and SHALL label the result "port open"/"no
  response" - never implying the rpc-server itself is confirmed healthy, since its protocol cannot be
  inspected over a plain TCP probe.
- THE SYSTEM SHALL cap the total pool at 4 nodes (main + 3 workers), enforced server-side, matching the
  roadmap's own "2-4 machines" scope and the range the capacity-estimate discount is anchor-checked
  against.
- THE SYSTEM SHALL NOT offer any per-worker throughput/speed estimate - Anthill cannot probe a remote
  machine's chip, so only a capacity estimate is offered, and the UI states this plainly.

## 3. Design

- `OrgSettings` gains four columns: `org_cluster_enabled`, `org_cluster_kind`, `org_cluster_main_mem_gb`,
  `org_cluster_workers` (JSON array, mirroring `org_council_members`'s convention).
- `anthill/hosting/sizing.py` gains `cluster_max_params_b()` (sums each node's own `usable_gb()`, then
  applies the unvalidated `_CLUSTER_NETWORK_EFFICIENCY` discount) and `model_fits_cluster()`.
- `anthill/hosting/cluster.py` (new) gains `tcp_reachable()`/`worker_reachability()` - a raw socket
  connect, nothing more.
- A new `POST /settings/organization/cluster` route, separate from the existing `settings_org_post`
  (whose council-diffing logic is unrelated to this orthogonal config).
- The settings page renders cluster config as an additive block inside the existing "Connect a model
  server you already run" `<details>` (already unconditionally available, already outside the main
  settings `<form>`), gated `{% if cfg.org_provider == 'onprem' %}` - not a new `org_provider` value,
  since that would pull in the `Provisioner` registry and the council fit-gate machinery, neither of
  which apply to "machines an org already owns, with zero provisioning."

## 4. Explicitly out of scope

Anthill launching/managing `rpc-server`/`llama-server` processes; per-worker speed estimates; mixed
Apple Silicon/GPU pooling in one cluster; composing with #636/#671's multi-GPU-per-node work (a worker
that is itself a multi-GPU box); a new `anthill/inference/cluster.py` backend class; any reuse of
`anthill/mesh_auth.py`/`anthill/orchestrator/`/`anthill/node_agent/`.

## 5. Acceptance criteria

1. Enabling cluster pooling on a non-onprem org is rejected server-side; saving a non-onprem provider
   on the main form clears `org_cluster_enabled` if it was on.
2. The settings page renders an honest capacity estimate and per-worker reachability badges, computed
   at GET-render time, never persisted.
3. A model exceeding the cluster estimate renders a non-blocking warning; the save never fails on it.
4. More than 3 submitted workers are capped to 3 server-side, not just in the browser.
5. The new form is verified NOT nested inside the main settings form.
6. `ruff check`, `ruff format --check`, `mypy`, full test suite pass with zero failures.
