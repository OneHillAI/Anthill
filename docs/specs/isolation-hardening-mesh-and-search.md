# Spec: within-deployment isolation hardening (mesh auth + wiki search)

Status: accepted. Lane: `pillar:privacy`.

Two residuals from the tenancy-isolation audit. Both are *within a single deployment* (neither crosses
the org/install boundary, which is enforced separately by one-org-per-deployment + per-install keys),
but both are worth closing.

## 1. Mesh auth is secure by default

`require_mesh` (`anthill/mesh_auth.py`) gates the orchestrator's state-seeding endpoints (node
registration, wiki promotion, central-cache publish/search) and the web app's metric-ingest endpoint.
Previously, when `ANTHILL_MESH_TOKEN` was unset the gate was an **open no-op** (warn-once), so a
multi-node org that exposed its orchestrator without setting the token accepted unauthenticated node
registrations and wiki promotions that every agent then grounds on.

Now the gate is **closed by default** when no token is set: it returns 401 unless the operator sets
`ANTHILL_MESH_ALLOW_INSECURE=1`. A standard single-node deployment never calls these endpoints (the
only caller is the multi-node `anthill node` CLI, which sends the token), so this changes nothing for
it; a deployment that genuinely wants an unauthenticated mesh must opt in **explicitly** rather than
get an open boundary silently. Setting the token remains the recommended path. (Phase 3 replaces the
shared secret with Ed25519-signed manifests; this is the interim posture.)

## 2. Wiki (Meilisearch) retrieval is isolated per workspace

Optional Meilisearch retrieval (`anthill/search/meili.py`) upserted every scope's pages into **one
flat index** (`anthill-wiki`) keyed by `id = slug`. So a personal `roadmap` page and the org `roadmap`
page collided (last write wins), polluting each other's ranking within a deployment. Disclosure was
already prevented at read time (retrieval only keeps slugs present in the querying workspace and serves
that scope's own file), so this was a search-correctness/recall issue, not a leak - but it means one
user's edits could shift another scope's ranking.

Each `Workspace` now has a distinct index, `Workspace.meili_index` = `anthill-wiki-<sha256(root)[:16]>`,
keyed on the workspace root so index-time and query-time always agree. Every call site
(`workspace.reindex`, `wiki/ask.py`, `agent/tools.py`) passes it. Per-scope indexes never collide.

## Tests

- `tests/test_mesh_auth.py`, `tests/test_metrics_auth.py`: unset token is now 401 by default; the
  `ANTHILL_MESH_ALLOW_INSECURE=1` opt-in restores the open behaviour; set-token enforcement unchanged.
- `tests/test_meili_scope_isolation.py`: distinct + stable + valid-uid index per workspace, and each
  search/index call targets that workspace's own index.
