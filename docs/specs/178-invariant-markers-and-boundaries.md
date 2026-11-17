# Spec: Invariant markers that stay covered, and package boundaries that stay put

Status: implemented. Lane: `pillar:platform`. Issue: #178.

## Problem

`asdd-invariants.yml` runs `pytest -m "privacy_invariant or knowledge_invariant or model_invariant"` to guard the
core pillars' MUST-invariants. Only 5 test functions in 4 files carried those markers, while dozens of existing
tests guard the same invariants without them, so the job exercised almost nothing. Nothing stopped the few tags
from being deleted or mistyped either: the job stays green on whatever is left. Separately, the module layering
ARCHITECTURE.md describes (a node cannot impersonate the orchestrator; the domain packages sit under the web
app) was checked only by the spec review lens reading diffs.

## Requirements

1. The existing tests that guard a MUST-invariant in the invariant manifest SHALL carry the matching marker,
   applied to the whole module when every test in it is on-invariant. Tagged: privacy (P1 consent fail-closed, P2
   scrub-before-egress and audit, tenant and owner isolation), knowledge (K2 retrieved content is data, not
   instructions), model (the signed catalog, training-data eligibility, the promotion gate's scoring).
2. A test SHALL fail when an invariant marker keeps fewer tagged tests than its floor, and when a marker that looks
   like an invariant marker (contains "invar") is not one registered in `pyproject.toml`, so a deleted or
   mistyped tag cannot drain coverage unnoticed. The count is made with `ast`, not a collection run.
3. A test SHALL fail on an import, anywhere in a file including inside a function, that crosses a boundary:
   `common` imports no other anthill package; `wiki`, `inference`, `hybrid` and `node_agent` import neither
   `web` nor `orchestrator`; `orchestrator` does not import `node_agent`.
4. Only the packages in a fixed set (`agent`, `cli`, `cloud`, `desktop`, `training`) MAY import `anthill.web`.
   That set is today's reality, not an endorsement, and can only shrink: the test fails on a new importer and on
   an entry that no longer imports it.
5. No dependency is added; both checks are static, offline and model-free.

## Known limits

- The invariant manifest lives in the private `internal` repo and its statuses date from June 2026. Tagging
  follows what each test asserts against its MUSTs, not a list from the issue: three of the issue's files are
  left out because they are not charter MUSTs (`test_meili_scope_isolation` is a ranking collision,
  `test_no_raw_errors` is error-text hygiene, `test_wiki_personal_review` is a stuck-queue fix).
- MUSTs with no live guarding test (P1's per-surface suite, T1, T5, K1 across all ingest paths) are still gaps;
  tagging does not create them.
- `asdd-invariants.yml` stays report-only and is not edited here. Making it a required check is a branch
  protection change for the repo owner, and the job skips docs-only PRs, so as a required check it would sit
  pending on them (see the launch gate note in `internal#16`).
- The counts are of test functions; a parametrized test counts once.
- The Presidio test skips when the optional engine is not installed, so on a plain install one tagged test is
  skipped, not run.
- Import checks see the module a name comes from, not what is used at run time, and do not see imports built from
  strings (`importlib.import_module`).

## Acceptance

- `pytest -m "privacy_invariant or knowledge_invariant or model_invariant"` selects 101 tests (privacy 59,
  knowledge 23, model 19, counting parametrized cases) and they pass.
- Removing the markers from three privacy files, or mistyping one marker, fails the marker test.
- Adding an import of `anthill.web` to `wiki` (top level, relative, or inside a function), of `orchestrator` to
  `node_agent`, of `node_agent` to `orchestrator`, or of any package to `common` fails the boundary test; so does
  a new package importing `web` and a stale entry in the web importers set.
- Tests: `tests/test_invariant_markers.py`, `tests/test_architecture_boundaries.py` (model-free).
