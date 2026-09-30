# Local MoE CPU-offload fit tier

## Problem

The local model picker hard-hides any model whose 4-bit resident footprint exceeds the machine's **fast**
memory pool (`fit_tier` returns `too_large`; the picker filters those out at `app.py`). On a discrete-GPU
box that pool is **VRAM**; on Apple Silicon it is unified memory. This is correct for dense models and for
Apple (Metal/MLX has no memory spillover), but it **under-offers on a GPU box running a Mixture-of-Experts
model**:

- Ollama (llama.cpp) already **auto-offloads** layers/experts to system RAM when a model exceeds VRAM, so
  such a model *serves today* with no new code.
- Decode is memory-bandwidth-bound, and an MoE reads only its **active** experts per token. So a big MoE
  with routed experts in system RAM stays usable, because the per-token working set is `active_b`, not
  `params_b`. Anthill already models exactly this physics for the fits-in-pool case
  (`estimate_tokens_per_second` uses `active_b` and the chip's bandwidth; `is_usable_speed` applies the
  `_MIN_USABLE_TOK_S = 10.0` floor; `_MOE_OVERHEAD_FACTOR = 0.45`).

So we grey out big MoE models a modest GPU + ample system RAM could actually run at tolerable speed. The
research pass that motivated this (AirLLM et al.) confirmed the inverse too: **dense** models offloaded to
CPU/SSD are unusably slow (they read all params per token), so the tier must be MoE-specific and
speed-gated, not a blanket "let anything run."

## Requirements

1. **A new fit state, `offload`** ("runs, slower"), between `tight` and `too_large`, offered (not hidden)
   only when a model genuinely runs at usable speed via CPU offload.
2. A model qualifies for `offload` **only if all** hold:
   - the box is a discrete-GPU box (`kind == "gpu"`)  -  Apple/unified stays fit-or-bust, unchanged;
   - its 4-bit resident footprint **exceeds VRAM** (would otherwise be `too_large`) but its total 4-bit
     footprint **fits VRAM + system RAM** (with headroom reserved for the OS, as `usable_gb` already does);
   - it is a **MoE** (`active_b > 0 and active_b < params_b`);
   - the **spill is modest** - most of the model still fits VRAM and only a bounded slice of experts
     offloads. A *mostly*-offloaded model is not reliably usable and its speed is dominated by PCIe + GPU
     generation, not something we can estimate (data: gpt-oss-120B offloaded is ~1.6 tok/s on a 3090 but
     ~8-10 on a 5090). Gate on the **offloaded fraction**, not just "fits VRAM + RAM";
   - its **offloaded** tokens/sec estimate clears `_MIN_USABLE_TOK_S`, where the estimate scales the
     working set by the offloaded slice over **system-RAM bandwidth** and is biased to under-offer (see
     Speed model - over-promising an unusable model is the failure to avoid, not under-offering a slow one).
3. A dense model, or a MoE whose offloaded estimate is below the floor, or any model on Apple/unified,
   stays `too_large` (honest: it would be unusably slow or cannot spill at all).
4. **Safe degrade:** if system-RAM capacity or its bandwidth can't be probed, do **not** offer `offload`
   (fall back to today's behavior). Never worse than current.
5. Do **not** touch the intelligence/capacity tiebreak (`(m.intelligence, m.params_b)`), per the guardrail
   established in the `onprem-moe-speedup` change  -  offload is a fit/speed axis, not a ranking axis.

## Design (additive, reuses existing machinery)

- **Hardware probe:** `local_hardware()` returns `(fast_pool_gb, kind)`. Add total system RAM alongside it
  for the `gpu` case (there is already `_posix_ram_gb()` / `_macos_mem_gb()` and `free_mem_gb()`), plus a
  conservative **system-RAM bandwidth** figure (DDR5 dual-channel ~ 60-90 GB/s; use a deliberately low
  default and treat unknown as "cannot estimate", exactly like `_apple_chip_bandwidth_gbps` returning
  `None`). VERIFY the default against a couple of real DDR5 measurements before finalizing (mirror the
  chip-bandwidth-table caution in `onprem-moe-speedup`).
- **Speed model:** extend `estimate_tokens_per_second` (today returns `None` for `kind != "apple"`) with a
  GPU-offload branch. Two calibration facts from the 2026 build guide (`Open Model Index/`) shape it:
  - **Do NOT reuse the Apple `_MOE_OVERHEAD_FACTOR = 0.45`** - it is unified-memory-calibrated (everything
    on one bus). On GPU offload the hot path (attention, router, shared experts, KV) stays on fast VRAM and
    only routed experts stream from RAM, so 0.45 *under*-predicts ~2.4x: a 35B-A3B on a 12 GB card + 32 GB
    RAM measures **~50 tok/s**, but `3B x 0.5 / ~70 GB/s x 0.45` gives ~21.
  - **The naive `active_b / RAM-bandwidth` model *over*-predicts for a large offloaded fraction** - it
    ignores that a mostly-offloaded model reads far more per token and is PCIe/GPU-gen bound. gpt-oss-120B
    (5.1B active) is ~1.6 tok/s on a 3090, not the ~27 the naive formula gives.
  So model the **offloaded working set**: roughly `active_b x offloaded_fraction x _BYTES_PER_PARAM_Q4`
  over system-RAM bandwidth, with a GPU-offload calibration constant derived from the two data points
  above and a deliberate conservative bias. When the spill is large or the machine can't be characterised,
  return `None` / `too_large` rather than an optimistic guess - **over-promising an unusable model is the
  failure mode to avoid.**
- **`fit_tier`:** add the `offload` branch per the qualification rules above; keep the existing
  `recommended` / `tight` / `too_large` outcomes otherwise.
- **Picker (`app.py` ~1542/1552):** treat `offload` as *fitting* (not filtered out), surfaced with a clear
  "runs, but slower (uses system RAM)" label so the user chooses knowingly. The `too_slow` exclusion and
  the intelligence ranking are unchanged.
- **Serving:** none required for v1  -  Ollama auto-offloads. Optional follow-up: pass `options.num_gpu`
  (`anthill/inference/ollama.py` already sends an `options` dict) or adopt `--n-cpu-moe`-style expert
  placement for better MoE offload speed, **pending a check of what the bundled Ollama version exposes.**

## Acceptance criteria

- A MoE that overflows VRAM by a **modest** slice on a `gpu` box with ample system RAM (e.g. a ~35B-A3B on
  a 12 GB card + 32 GB RAM, which really runs ~50 tok/s) is returned as `offload` (offered, labeled
  "slower") when its estimate clears the floor.
- A **mostly-offloaded** MoE (e.g. gpt-oss 120B on a 24 GB card, ~1.6 tok/s on older GPUs) stays
  `too_large` - the spill is too large and the speed is not reliably estimable.
- A dense model larger than VRAM stays `too_large`.
- A MoE whose offloaded estimate is below `_MIN_USABLE_TOK_S` stays `too_large`.
- Apple/unified: no `offload` ever (fit-or-bust unchanged).
- Unknown system-RAM capacity or bandwidth -> no `offload` offered (degrades to current behavior).
- The estimate is sanity-checked against the two build-guide data points (35B-A3B ~50 tok/s; gpt-oss-120B
  ~1.6-10 tok/s across GPU generations); the model never predicts materially faster than measured.
- The `(intelligence, params_b)` tiebreak is byte-for-byte unchanged.
- Tests use real catalog entries, not synthetic fixtures (per the repo convention).

## Out of scope

- MLX/Apple offload or SSD paging (no usable spillover  -  deliberately excluded).
- Dense-model offload (unusably slow by construction).
- `--n-cpu-moe` / explicit expert placement (a serving optimization, tracked as the optional follow-up).
