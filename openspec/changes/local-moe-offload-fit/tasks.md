# Tasks

- [ ] Add total system RAM to the hardware probe for `gpu` boxes in `anthill/hosting/sizing.py` (reuse
  `_posix_ram_gb()` / `_macos_mem_gb()`; do not re-probe VRAM). Keep `local_hardware()`'s existing
  `(mem_gb, kind)` contract intact for all current callers - expose the RAM figure additively (a new
  helper or an optional field), never by changing the existing return shape.
- [ ] Add a conservative **system-RAM bandwidth** constant/helper (DDR5 dual-channel ~60-90 GB/s; VERIFY
  against real measurements first). Return `None` for unknown, same degrade-safe contract as
  `_apple_chip_bandwidth_gbps` - callers must treat `None` as "cannot estimate", never guess.
- [ ] Extend `estimate_tokens_per_second` with a `kind == "gpu"` offload branch that models the
  **offloaded working set**, not raw active params: roughly `active_b x offloaded_fraction x
  _BYTES_PER_PARAM_Q4` over the system-RAM bandwidth, with a NEW GPU-offload calibration constant.
  Do NOT reuse `_MOE_OVERHEAD_FACTOR` (0.45) - it is unified-memory-calibrated and under-predicts GPU
  offload ~2.4x. Calibrate + sanity-check against the two data points in the spec (35B-A3B ~50 tok/s;
  gpt-oss-120B ~1.6-10 tok/s); bias conservative and never predict faster than measured. Leave the Apple
  branch and the `None`-degrade behavior unchanged.
- [ ] Add an `offload` outcome to `fit_tier` per `docs/specs/local-moe-offload-fit.md`: only for
  `kind == "gpu"`, a MoE (`0 < active_b < params_b`) that overflows VRAM by a **modest** slice (gate on the
  offloaded fraction - a mostly-offloaded model is PCIe/GPU-gen bound and stays `too_large`), fits VRAM +
  system RAM (OS headroom reserved as `usable_gb` already does), AND whose offloaded estimate >=
  `_MIN_USABLE_TOK_S`. Dense models, sub-floor MoEs, mostly-offloaded MoEs, Apple/unified, and
  unknown-bandwidth boxes stay `too_large` / unchanged.
- [ ] Surface `offload` in the picker (`anthill/web/app.py` ~1542/1552): count it as *fitting* (not
  filtered like `too_large`) and label it "runs, but slower (uses system RAM)". Do not change the
  `too_slow` handling or the intelligence ranking.
- [ ] Do NOT modify the `(m.intelligence, m.params_b) > (best.intelligence, best.params_b)` tiebreak.
- [ ] Tests using REAL catalog entries (a big MoE like gpt-oss 120B, a dense model, a small MoE), asserting:
  MoE-over-VRAM-under-RAM-and-fast-enough -> `offload`; dense-over-VRAM -> `too_large`; slow MoE ->
  `too_large`; Apple -> never `offload`; unknown RAM/bandwidth -> no `offload` (current behavior).
- [ ] Follow-up (SEPARATE change, do not build here): serving-side MoE offload control via
  `options.num_gpu` / `--n-cpu-moe`, once the bundled Ollama version's support is confirmed.
