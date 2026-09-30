# Spec: Skip the runtime cross-check under memory pressure

Status: implemented. Lane: `pillar:model`. Issue: #413 (direction 3).

## Problem

The runtime verifier (`anthill/verify/verify.py`) cross-checks a consequential output by loading a
**second, different-family** model and asking it to judge the result. That second model loads **while
the producer model is still resident** - two models in memory at once. On a memory-constrained Mac
(unified memory, no discrete VRAM) that concurrency saturates RAM and the app freezes: the exact
failure #413 reports. The over-sized-model and picker angles of #413 are handled elsewhere (the
resident-footprint sizing fix #490, the tight-tier picker #416, and the configured-model pin in
PR #503); this is the remaining runtime angle - don't load a co-resident second model when there is
no headroom for it.

## Policy

- Before loading the cross-check model, read **free** (not total) system memory. If it is below a
  floor (`_VERIFIER_MIN_FREE_GB = 6.0`, roughly a mid-size local model's footprint), **skip the model
  cross-check** and return `CrossCheck(ok=None, reason="skipped: ... memory pressure")`.
- Skipping is safe by construction: the deterministic, model-free checks still run, and an `ok=None`
  cross-check makes the verdict fall back to **deterministic-only + always surface for review**
  (`needs_review=True`). The verifier is advisory - it never blocks - so degrading to no-cross-check
  under pressure loses a signal, never correctness.
- Free memory is read best-effort and cross-platform (`anthill/hosting/sizing.py:free_mem_gb`): macOS
  parses `vm_stat` (free + inactive pages x page size), Linux reads `/proc/meminfo` `MemAvailable`.
  When free memory **can't** be determined (unknown platform, command failure) the value is `None` and
  the guard does **not** fire - it must not skip the cross-check on machines where it can't measure
  pressure, only where it can prove there is none.

## Acceptance criteria

- With free memory below the floor, `default_crosscheck(...)`'s callable returns `ok=None` and a reason
  containing "memory pressure", **without** attempting to load a model (a verifier model is still
  chosen, so `model` is populated, but it is not invoked).
- With free memory unknown (`free_mem_gb()` returns `None`) or ample, the guard does not fire and the
  cross-check proceeds on its normal path.
- `_parse_vm_stat_free_gb` converts a `vm_stat` sample to GB using the reported page size, and returns
  `None` for unparseable input.
- Covered by `tests/test_verifier_memory_guard.py`.
