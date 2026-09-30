# Tasks: PR #661 Tier 5 distributed local pooling

## Build steps

1. `anthill/web/db.py`: add four `OrgSettings` columns - `org_cluster_enabled` (Boolean, default
   False), `org_cluster_kind` (String(10), default "apple"), `org_cluster_main_mem_gb` (String(10),
   default ""), `org_cluster_workers` (Text, default "[]", JSON array of
   `{"label","host","port","mem_gb"}` - mirrors `org_council_members`'s shape). No manual
   `_ensure_columns` entry - the generic `migrate.ensure_columns()` additive pass already picks up
   plain new columns automatically (confirmed this session for the equivalent case).
2. `anthill/hosting/sizing.py`: add `_CLUSTER_MIN_NODES = 2`, `_CLUSTER_MAX_NODES = 4`,
   `_CLUSTER_NETWORK_EFFICIENCY = 0.55` (comment must say explicitly: NOT empirically calibrated,
   unlike `_MOE_OVERHEAD_FACTOR`; give the anchor-check arithmetic as a sanity note, not a derivation).
   Add `ClusterSizing` dataclass (`max_params_b`, `node_count`, `raw_sum_gb`, `note`) and
   `cluster_max_params_b(node_mem_gb, *, kind="apple", context_k=8.0, concurrency=1,
   efficiency=_CLUSTER_NETWORK_EFFICIENCY)` (sums `usable_gb()` per node, applies the discount, builds
   the honesty note - stronger wording when `node_count` is outside `[_CLUSTER_MIN_NODES,
   _CLUSTER_MAX_NODES]`), and `model_fits_cluster(params_b, node_mem_gb, *, kind="apple",
   efficiency=...)` (mirrors `model_fits_vram`'s shape).
3. New `anthill/hosting/cluster.py`: `tcp_reachable(host, port, *, timeout=2.0) -> bool` (plain
   `socket.create_connection`, catches everything, never raises) and `worker_reachability(workers, *,
   check=tcp_reachable) -> list[dict]` (injectable `check` for tests, mirrors `plane_routing`'s
   injectable-callable convention). Each returned dict keeps the worker's own fields plus a `reachable:
   bool`.
4. `anthill/web/app.py`:
   - `_cluster_workers_from_cfg(cfg)` helper (mirrors `_members_from_cfg`) - parses
     `org_cluster_workers` JSON, tolerates malformed/missing data by returning `[]`.
   - New `POST /settings/organization/cluster` route: `_require_admin`; parses `org_cluster_enabled`,
     `org_cluster_kind`, `org_cluster_main_mem_gb`, `worker_count` + indexed `worker_label_i/
     worker_host_i/worker_port_i/worker_mem_gb_i` via `await request.form()` (same shape as the
     existing reviewer-row parsing); rejects enabling when `cfg.org_provider != "onprem"`
     (`error=cluster_needs_onprem`); caps parsed workers at 3 server-side (not just client-side);
     saves the four fields; `audit.log(db, "settings.org_cluster_saved", ...)`; redirects
     `/settings/organization?saved=1`.
   - `settings_org_get`: context gains `cluster_workers` (from `_cluster_workers_from_cfg` +
     `worker_reachability`), `cluster_sizing` (from `cluster_max_params_b`, only when
     `cfg.org_cluster_enabled`), `cluster_model_fits` (from `model_fits_cluster` against the org's
     currently-selected model's params, only when both a model is selected and cluster pooling is on).
   - `settings_org_post` (the EXISTING main-form handler): one added line - after resolving the new
     `org_provider`, if it is not `"onprem"`, force `cfg.org_cluster_enabled = False`.
5. `anthill/web/templates/settings_organization.html`: inside the existing "Connect a model server you
   already run" `<details>` (do not touch `org_model_endpoint`/`org_model_key_enc`/the connect form
   itself), add a new nested block gated `{% if cfg.org_provider == 'onprem' %}`:
   - Checkbox + `?` popover (matching the `.help`/`.help-pop` convention already used elsewhere in this
     file) disclosing that capacity numbers are self-reported.
   - `kind` select + main-node-memory input.
   - Worker rows: adapt the EXISTING `reviewer-rows`/`reviewer-row-template`/add-remove-button JS
     pattern already in this file (do not invent a new one) - label/host/port/mem_gb fields, JS-capped
     at 3 rows.
   - Its OWN `<form method="post" action="/settings/organization/cluster">` - verify it is NOT nested
     inside the page's main `<form action="/settings/organization">` (it must be a sibling, positioned
     appropriately relative to that form's own open/close tags - check with a `document.
     querySelectorAll('form')` browser test during live verification, not just a source-code read).
   - Always-rendered estimate line + honesty note; per-worker reachability badges (labeled "port open,"
     not "server healthy"); a persistent, non-blocking warning banner only when `cluster_model_fits` is
     False - never a save-blocking error.
6. `docs/specs/local-vs-frontier-capability-roadmap.md`: correct the Tier 5 mTLS/mesh-reuse claim in
   place, and add a "Status: done" note under Tier 5 (matching Tier 3's status-note pattern).
7. New tests:
   - `tests/test_sizing_cluster.py`: `cluster_max_params_b`/`model_fits_cluster` pure-math cases with
     worked-arithmetic comments (style of `tests/test_onprem_council_fit.py`); below-min-nodes and
     above-max-nodes note-wording cases.
   - `tests/test_cluster_reachability.py`: `tcp_reachable` against a real bound-but-unaccepting local
     socket (reachable) and an unused port (unreachable) if fast/reliable enough for CI, else fully
     injected; `worker_reachability` with an injected fake `check` covering mixed reachable/unreachable
     workers.
   - `tests/test_org_cluster_settings.py`: using the existing `_app`/`_auth` fixture convention from
     `tests/test_council_members.py` - onprem-only gate (rejects enabling on a non-onprem org),
     provider-switch auto-clear (saving a non-onprem provider via the main settings form clears
     `org_cluster_enabled`), server-side worker-count cap (posting more than 3 workers keeps only 3),
     GET-render estimate/warning/reachability rendering (mocking `worker_reachability`'s `check`).
8. `ruff check`, `ruff format --check`, `mypy`, full test suite - zero failures.
9. `changelog.d/<pr-number>.added.md` once a PR number exists.

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section.
