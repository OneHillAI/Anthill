# Spec: a page promoted by a node is a proposal, not a write

Status: implemented. Lane: `pillar:knowledge`. Part of the knowledge base update (epic #270): a page that comes from another
machine is a proposal, and a person decides. Builds on `docs/specs/isolation-hardening-mesh-and-search.md` (the mesh token).

## Problem

The orchestrator hub accepts `POST /wiki/promote` from a registered node and wrote the page straight into its wiki, replacing any
page with the same name. The hub has no review queue, so nobody looked at the page before every agent that reads that wiki
could use it.

## Requirements

1. **Refused by default.** `POST /wiki/promote` returns 403 ("this hub has no review queue ...") and writes nothing, for every
   caller, unless the operator sets `ANTHILL_MESH_PROMOTE_DIRECT` to `1`, `true`, `yes` or `on`. The mesh token is still required
   first (401 without it).
2. **When an operator opts in**, a registered node may add a NEW page. A page with that name that already exists is never
   replaced (409). The page is marked as written by the node (`model=node:<id>`), so its record says it is model-written and
   not yet checked; the index and the log (`promote | <slug> from <node>`) are updated. An unregistered or timed-out node is
   refused (403). The slug is reduced to a plain name as before.
3. The `anthill promote` command shows the hub's reason when it is refused.

## Known limits

- With the opt-in a page still lands in the wiki without a person approving it; it is unchecked and labelled in answers, and it
  cannot replace an existing page. A review queue for the hub is separate work.
- Existing pages written by earlier promotions are not changed.

## Acceptance

`tests/test_mesh_promote_needs_review.py`: refused by default with the wiki unchanged; the token is required in both modes; with
the opt-in a new page is added, marked and unchecked, and logged; an existing page is never replaced; an unregistered node is
refused; a slug cannot leave the wiki.
