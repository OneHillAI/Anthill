# Spec: fit and speed estimates from model metadata and an exact chip table

Status: implemented. Lane: `pillar:model`. Issue: #223.

## Problem

`anthill/hosting/sizing.py` charges every model a flat 0.1 GB of KV cache per 1,000 tokens, though the real cost
differs several-fold: qwen2.5:3b needs 36.9 KB per token, granite3.2-vision:2b 81.9 KB. The chip lookup matched
by substring, so "Apple M1 Pro" resolved to the base M1 (70 GB/s, real 200), "Apple M2 Max" to the base M2 and
"Apple M5 Max" to the base M5; the base M3 had no entry. Chips whose bandwidth depends on the GPU core count
(M3, M4 and M5 Max) had one figure. The speed estimate has no efficiency term: for a 9B model on a base M4 it
says 26.7 tok/s where #97 measured 12.7.

## Requirements

1. **Exact chip table.** `_APPLE_CHIP_BANDWIDTH_GBPS` holds Apple's published unified-memory bandwidth for the
   M1 to M5 families. The chip name must match whole (`Apple M1 Pro` is the M1 Pro). A chip with two published
   bandwidths (M3 Max 300 or 400, M4 Max 410 or 546, M5 Max 460 or 614) is looked up with the GPU core count
   (`system_profiler`, read only for those chips and cached). A chip not in the table, a Max chip whose core
   count cannot be read, and a core count not in the table return None. The base M1 figure (68.25) is the
   memory bus arithmetic, since Apple states none; the M6 (bandwidth depends on memory size) and M5 Ultra are
   left out until verified.
2. **Metadata.** `anthill/hosting/model_memory.py` reads layers, KV heads (one number or one per layer), key and
   value length (else embedding divided by heads), sliding window, and recurrent layers from `/api/show`, and
   works out the cache the way llama.cpp's server allocates it, which is what Ollama runs models in:
   cells rounded up to 256; a sliding-window layer holds the window plus one micro-batch (512, llama.cpp's
   default); recurrent layers of a hybrid model (qwen3next, qwen35, qwen35moe) hold a constant f32 state and no
   per-token cache; cache types f16, q8_0, q4_0. Missing, odd or not understood metadata (an embedder, a layer
   count that disagrees with an array, an unknown cache type) gives None, and the flat figure stays as the
   fallback. Where a rule is uncertain the cache is over-counted, never under-counted: a per-layer sliding-window
   array is counted as all global layers, and a window key on an architecture not known to use it is ignored
   (llama.cpp ignores it too).
3. **Fit rules.** `fit_tier`, `resident_gb`, `usable_gb`, `max_params_b` take an optional `kv_gb_per_k`. Left out,
   behaviour is unchanged. No caller passes it yet.
4. **Four figures.** `estimate_memory` and `measured_memory` return weight, allocated, resident and peak in
   decimal GB, each marked `estimated` or `measured`. Allocated is weights, cache and compute buffer (what
   `ollama ps` shows as SIZE); resident is the part in GPU or unified memory (SIZE_VRAM); peak adds the existing
   runner working set. Measured figures come from an `/api/ps` entry; peak is always estimated. An unknown file
   size is None, never 0.
5. **Dense speed estimate, experimental.** `estimate_dense_decode_tokens_per_second(weight_gb)` is the exact-table
   bandwidth divided by the model file size, times a 0.7 efficiency prior. It is for dense models only.
   `estimate_tokens_per_second` and the picker's speed floor are not changed: existing tests pin their
   calibration (a MoE and a dense 70B anchor). The floor does read the chip bandwidth, so requirement 1 changes
   its verdict on the chips that resolved wrongly before (M1 Pro, M2 Max, M5 Max and similar).

## Known limits

- Two real architectures were recorded (qwen2, granite) because only two chat models are installed on the machine
  used. The sliding-window, gpt-oss and hybrid cases are tested with fixtures shaped after the documented keys
  and were not recorded from a model. No gemma4 or qwen3.5 model was loaded.
- The compute buffer (22 KB per token) was read off those same two models on Ollama 0.40.7, so their agreement is
  not independent evidence for it. The cache itself is derived from the metadata and checked by hand.
- The micro-batch (512) is llama.cpp's default; Ollama's own batch flags were not read. A larger one makes a
  sliding-window cache bigger than counted here.
- The hybrid state formula is llama.cpp's generic one; the qwen35 layers were not confirmed to match it. A
  per-layer recurrent list is not decoded and gives None.
- A vision model's file includes an image encoder that is never loaded, so its weight and allocated figures run
  high (granite3.2-vision:2b: 44% over `ollama ps`).
- The 0.7 efficiency prior has two points on one chip (base M4: qwen2.5:3b 0.68, qwen3.5:9b about 0.7). Published
  results for a 70B model on Max chips sit higher, so it leans low. Nothing is calibrated per tier.
- The memory and chip figures come from a research pass over Apple's and Ollama's pages, read through a page
  summariser, not as raw source. The M1 Pro 14-inch 10-core and M3 Pro 12-core rows were flagged for a spot check.
- `ollama ps` shows the model's allocation after load; this change does not read it anywhere in the app.

## Acceptance

- `tests/test_apple_chip_bandwidth.py` (new): M1 Pro, M2 Max, the base M3 and M5 Max resolve to their own figures;
  an unseen chip, a Max chip with unreadable or unlisted cores and an odd name return None; the dense estimate is
  within 10% of the measured qwen2.5:3b run.
- `tests/test_fit_from_metadata.py` (new): five architectures within 10% of a hand calculation; missing or odd
  metadata gives None; estimate against recorded `/api/ps` readings (qwen2.5:3b within 0.3% at 4,096 tokens, the
  growth with the window within 10% for both models); measured figures come from `/api/ps`.
- `tests/test_hosting.py`, `test_offload_fit.py`, `test_moe_speedup.py`, `test_model_picker.py` and
  `test_onprem_council_fit.py` still pass (one chip-lookup test sets the GPU core count for the M4 Max).
