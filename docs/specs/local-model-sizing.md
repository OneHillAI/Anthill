# Spec: Local model sizing must not over-recommend on small Macs

Status: implemented. Lane: `pillar:model`. Issue: #490 (root cause under #413 freeze; pairs with #416).

## Problem

`anthill/hosting/sizing.py` recommends the largest open model that runs *well* on a box. For Apple
Silicon it sized the model from a flat fraction of unified memory (`_APPLE_USABLE = 0.66`) and compared
that budget to a model's **raw q4 weights** (`params * 0.55`). It ignored the model's real **resident
footprint** - the runner + KV working set a served model needs *beyond* its weights. On small machines
the budget was far too optimistic, so the "recommended" model starved or froze the box:

- a **16 GB** Mac was told to run a **14B** (weights ~7.7 GB, but resident ~10 GB) and froze (the
  founder's M4; they run an 8B);
- an **8 GB** Mac was told to run an **8B** with ~1 GB to spare.

24 GB and up were already fine, so this is specifically the small-memory case.

## Requirements

- Size the local (Apple Silicon) recommendation against the model's **resident footprint** - weights
  **plus** the KV budget **plus** a fixed runner working-set floor - not raw weights. Local serving is
  single-user (concurrency 1, 8k context).
- The recommendation must respect the founder's ground-truth anchors (single-user):
  - 16 GB -> at most an **8B** (never the 14B that froze the box);
  - 8 GB -> at most a **3B** (never an 8B);
- and must not regress the machines that were already fine: 24 GB -> 14B, 32 GB -> 32B, 64 GB -> 70B.
- The cloud-GPU path (fp16 / AWQ ceilings) is a different regime and must be **unchanged**.

## Acceptance criteria

- `recommend(mem, kind="apple", concurrency=1)` returns 3B @ 8 GB, 8B @ 16 GB, 14B @ 24 GB, 32B @ 32 GB,
  70B @ 64 GB.
- No family in `recommend_by_family(16)` offers a 14B; none in `recommend_by_family(8)` offers an 8B.
- A larger context/concurrency still lowers the recommendation (the KV budget is preserved).
- `cloud_gpu_max_params(80)` is unchanged (~37B at fp16).
- Covered by `tests/test_sizing_local.py` (pure, model-free).

## Notes

The issue also suggested `usable = min(mem*0.66, mem - OS_RESERVE)`; a ~6-7 GB fixed reserve there
overshoots the 8 GB anchor (nothing fits). Reserving a fixed **runner floor** on top of the unified-memory
fraction, and comparing resident footprint, hits every anchor without that inconsistency. This fix is the
recommender only; surfacing a pre-activation warning is #416.
