# PR #661 Tier 5: distributed local pooling (an org's own LAN machines)

## Why

`docs/specs/local-vs-frontier-capability-roadmap.md`'s Tier 5 section proposes pooling 2-4 of an org's
own LAN machines (via llama.cpp's `rpc-server`) to push the local serving ceiling from ~70B to roughly
~150B params - a moderate ceiling lift for orgs that already own several boxes, explicitly NOT a path
to 1T-class models. This is the last tier in the #661 roadmap; Tiers 0-4 are already shipped.

## Two claims in the roadmap doc corrected before building anything

The roadmap doc says Tier 5 should reuse "the mesh's mTLS/trust layer" (`anthill/mesh_auth.py`) for
node discovery. Verified against real code: `mesh_auth.py` is a shared-secret **bearer token**
(`require_mesh()` does an HMAC constant-time compare of an `Authorization: Bearer <ANTHILL_MESH_TOKEN>`
header; `mesh_headers()` emits it) - there are zero certificates or mTLS anywhere in this codebase
(grepped `mtls|certificate|ssl_context|x509|tls` across `anthill/orchestrator/`, `anthill/node_agent/`,
`mesh_auth.py`: zero matches). Even corrected to "bearer token," it still does not apply here:
`require_mesh()`/`mesh_headers()` protect HTTP endpoints, and llama.cpp's `rpc-server` protocol is not
HTTP - there is no HTTP surface on the worker side for a bearer token to gate. Nothing from
`anthill/mesh_auth.py`/`anthill/orchestrator/`/`anthill/node_agent/` is reused by this change.

Separately: llama-server, once launched with `--rpc host1:port1,host2:port2,...`, still exposes the
exact same OpenAI-compatible `/v1` HTTP API to a client - the clustering is transparent. So **no new
inference backend class is needed**. `anthill/web/plane_routing.py`'s `plane_inference()` already
returns `backend="openai", base_url=<org_model_endpoint>` for the org plane, and every existing code
path (`OpenAICompatBackend.chat/chat_stream/chat_with_tools/health`, the
`/settings/organization/connect` validation flow) works unmodified once an admin points
`org_model_endpoint` at their pool's main node's `/v1` URL. `config.backend` never needs a third value.

## What this change actually adds

Not provisioning, not process management - Anthill never SSHes into any machine or launches
`rpc-server`/`llama-server` (the admin sets those up themselves, per a short runbook). What's added:

1. A way for an admin to describe their pool (the main node's memory + up to 3 worker machines' labels/
   host/port/memory) alongside the existing "connect a server you already run" flow, since the main
   node's actual chat endpoint already IS `org_model_endpoint` - this only adds descriptive metadata.
2. A capacity estimate (`anthill/hosting/sizing.py`'s new `cluster_max_params_b()`) - the naive sum of
   each node's `usable_gb()`-derived capacity, times a conservative, explicitly-labeled-as-unvalidated
   network-efficiency discount (`_CLUSTER_NETWORK_EFFICIENCY = 0.55`) - anchor-checked against the
   roadmap's own "~150B for 2-4 machines" framing, not derived from any real benchmark (none exists in
   this codebase or session, unlike `_MOE_OVERHEAD_FACTOR`, which cites published data).
3. A lightweight TCP reachability check (`anthill/hosting/cluster.py`'s `tcp_reachable()`) - since
   `rpc-server`'s protocol is not HTTP, this can only ever say "something is listening on that port,"
   never "the rpc-server is healthy," and is labeled as such wherever it renders.

## What already exists and is reused

- `anthill/web/plane_routing.py`'s `plane_inference()` / `anthill/inference/openai_compat.py`'s
  `OpenAICompatBackend` - the entire chat path, unmodified.
- `anthill/web/app.py`'s `POST /settings/organization/connect` route and `org_model_endpoint`/
  `org_model_key_enc` fields - the main node's connection details, unmodified.
- `OrgSettings.org_council_members`'s JSON-array-of-dicts convention - mirrored by the new
  `org_cluster_workers` field.
- `settings_organization.html`'s Council-reviewer-rows indexed-field + add/remove-button + `<template>`
  JS pattern - mirrored for the worker-row list, with a hard cap of 3 rows.
- `docs/specs/helper-text-wave2.md`'s `.help`/`?`-popover convention - used to disclose that capacity
  numbers are self-reported, not measured.
- `anthill/hosting/sizing.py`'s `usable_gb()` - called once per node, then summed and discounted; no
  changes to `usable_gb()` itself.

## Scope decisions made (not left open)

- **Warn, never hard-block**, when the org's selected model exceeds the cluster capacity estimate. The
  estimate rests on self-reported, unmeasured node memory and an unvalidated discount constant -
  matching this codebase's own honesty precedent (Tier 1's explicit "UNVERIFIED" framing;
  `_MOE_OVERHEAD_FACTOR`'s comment distinguishing calibrated-from-real-data vs. a guess) - and unlike
  the cloud-GPU fit gate, no money is on the line here to justify blocking a save.
- **Hard cap of 4 total nodes** (main + up to 3 workers), matching the roadmap's own "2-4 machines"
  scope and the fact the discount constant is only anchor-checked for that range. Enforced server-side
  in the new route, not just in the add/remove-row JS.
- Cluster config is placed as an additive block **inside the existing "connect a server you already
  run" `<details>`**, gated `{% if cfg.org_provider == 'onprem' %}` - NOT a new `org_provider` value.
  Adding a new provider value would pull in `provision.PROVIDER_KEYS`/the `Provisioner` protocol
  registry and `_derive_council_member_selection`'s catalog+GPU-tier fit-gate shape, none of which fit
  "N boxes an org already owns, with zero provisioning." On-prem already has no fit-gate today (the
  existing code explicitly skips `servable_on_gpu` for on-prem: "unknown VRAM").
- **Self-reported, unverified node-memory numbers are an accepted v1 tradeoff** - Anthill cannot probe
  a remote LAN machine (no agent runs there, by design) - the UI discloses this via a `?` popover
  rather than implying a measurement.
- The new cluster-settings form is its OWN top-level `<form>`, a sibling to the main settings form, NOT
  nested inside it. `settings_organization.html` shipped a real, user-visible bug earlier this session
  from exactly that mistake (a nested `<form>` silently detaches everything after it, including the
  main "Save changes" button, from the outer form - invalid HTML, fixed in PR #672) - this change does
  not repeat it.

## Explicitly out of scope

- Anthill launching, SSHing into, or managing any `rpc-server`/`llama-server` process on any machine -
  the admin sets these up themselves; this change is configuration + estimation + reachability display
  only.
- Any per-worker throughput/speed (tokens/sec) estimate - `estimate_tokens_per_second()` needs `sysctl`
  on the running machine's own Apple Silicon chip and cannot read a remote box's; only a capacity
  estimate is offered, and the UI says so.
- Mixed Apple Silicon / dedicated-GPU pooling in one cluster - one `kind` for the whole pool.
- Composing with #636/#671's multi-GPU-per-node tensor-parallel work (a worker that is itself a
  multi-GPU box) - future work, not this change.
- A new `anthill/inference/cluster.py` backend class - explicitly not needed (see above).
- Reusing anything from the node mesh (`mesh_auth.py`/`orchestrator`/`node_agent`) - explicitly not
  applicable (see above).

## Acceptance criteria

1. An on-prem org admin can enable cluster pooling, set the main node's memory + kind, add up to 3
   worker rows (label/host/port/memory), and save; the four new `OrgSettings` fields persist correctly.
2. Enabling cluster pooling requires `org_provider == "onprem"`; attempting to enable it otherwise is
   rejected server-side, not just hidden client-side. Saving a NON-onprem provider on the main settings
   form automatically clears `org_cluster_enabled` if it was previously on.
3. The settings page renders an honest capacity estimate (`~XXXB params`, with its unvalidated-estimate
   note) and per-worker TCP reachability badges (labeled as "port open," not "server healthy"), computed
   at GET-render time, never persisted.
4. If the org's currently-selected model exceeds the cluster estimate, a persistent, non-blocking
   warning banner renders - the save is never refused.
5. The new `/settings/organization/cluster` form is verified NOT nested inside the main
   `/settings/organization` form (a real `document.querySelectorAll('form')` / `closest('form')` check
   in the browser, not just a source-code read - this exact bug type shipped invisibly once already).
6. New/updated tests cover: `cluster_max_params_b`/`model_fits_cluster` pure math (worked-arithmetic
   comments); `tcp_reachable`/`worker_reachability` with an injected fake check; the new route's
   onprem-only gate, worker-count server-side cap, provider-switch auto-clear, and GET-render
   estimate/warning/reachability rendering.
7. `docs/specs/local-vs-frontier-capability-roadmap.md` corrected in place (the mTLS/mesh-reuse claim)
   and given a "Status: done" note under Tier 5, matching how Tier 3's status note was added.
8. `ruff check`, `ruff format --check`, `mypy`, full test suite pass with zero failures. No em/en-dashes,
   no TODO/FIXME/XXX markers.
