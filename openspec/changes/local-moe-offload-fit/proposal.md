# Local MoE CPU-offload fit tier

Full spec: `docs/specs/local-moe-offload-fit.md`. This proposal is the "why now / what changes" narrative.

## Why

A research pass on "run large models on small-memory devices" (AirLLM and the broader 2026 landscape)
reached two conclusions:

1. **Disk-streaming tools like AirLLM are a dead end for interactive chat** (~0.01-1 tok/s; minutes per
   reply). Not worth integrating. Decode is memory-bandwidth-bound: anything that pages the per-token
   working set off SSD is demo-only.
2. **The genuine lever is MoE-aware CPU offload.** Because an MoE reads only its *active* experts per
   token, a big MoE with routed experts in **system RAM** stays usable at `active_b` speed  -  and Ollama
   (llama.cpp), which we already ship, **auto-offloads to CPU RAM today**. So the capability exists at the
   serving layer.

The gap is entirely in **sizing**: `fit_tier` hard-labels any model past the fast pool (VRAM on a GPU box)
as `too_large`, and the picker hides it. We therefore grey out big MoE models a modest GPU + ample system
RAM could actually run. Meanwhile the hard part  -  a bandwidth-bound, `active_b`-based, floor-gated speed
model  -  **already exists** (`estimate_tokens_per_second`, `is_usable_speed`, `_MIN_USABLE_TOK_S = 10.0`,
`_MOE_OVERHEAD_FACTOR = 0.45`), built by the `onprem-moe-speedup` change. This change extends that same
machinery to the offloaded case rather than inventing anything.

## What changes

Additive only (see the spec for the exact rules and acceptance criteria):

- **`local_hardware()`** also reports total system RAM (for `gpu` boxes)  -  reuses `_posix_ram_gb()` /
  `_macos_mem_gb()`  -  plus a conservative **system-RAM bandwidth** figure (unknown => `None`, same
  degrade-safe contract as `_apple_chip_bandwidth_gbps`).
- **`estimate_tokens_per_second`** gains a GPU-offload branch (today it returns `None` for non-Apple)
  modelling the **offloaded working set** (`active_b x offloaded_fraction`) over system-RAM bandwidth, with
  a NEW GPU-offload constant - NOT the Apple `_MOE_OVERHEAD_FACTOR` (0.45), which under-predicts offload
  ~2.4x. Calibrated + bounded by the build guide's real data points; biased conservative.
- **`fit_tier`** gains an **`offload`** outcome ("runs, slower") between `tight` and `too_large`, offered
  only for a **MoE** that overflows VRAM by a **modest** slice (gate on the offloaded fraction) **and**
  clears the speed floor. Dense models, sub-floor MoEs, *mostly*-offloaded MoEs, and Apple/unified stay
  `too_large` / fit-or-bust.
- **Picker** (`app.py` ~1542/1552) treats `offload` as *fitting*, labeled "runs, but slower (uses system
  RAM)", so the user chooses knowingly.
- **Serving:** no change for v1 (Ollama auto-offloads). Optional follow-up flagged: `options.num_gpu` /
  `--n-cpu-moe` expert placement, pending a check of the bundled Ollama version.

## Guardrails (do NOT touch)

- The `(m.intelligence, m.params_b)` capacity tiebreak in `recommend()` / `recommend_by_family()` stays
  byte-for-byte  -  offload is a fit/speed axis, not a ranking axis (same guardrail as `onprem-moe-speedup`).
- No offload on Apple/unified (no memory spillover exists there).
- Unknown RAM capacity or bandwidth => no offload offered; behavior identical to today.

## Verify before finalizing

- The default **system-RAM bandwidth** figure against a couple of real DDR5 measurements (mirror the
  chip-bandwidth-table caution in `onprem-moe-speedup`).
- What MoE-aware offload the **bundled Ollama version** actually exposes (naive per-layer vs `--n-cpu-moe`
  expert placement)  -  this bounds how aggressive the speed estimate may be. Start conservative.
