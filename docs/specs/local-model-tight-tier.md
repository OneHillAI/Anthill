# Spec: A "runs, but tight" tier in the local model picker

Status: implemented. Lane: `pillar:model`. Issue: #416 (pairs with #490 recommender fix; related to #413 freeze).

## Problem

The Local model page surfaces hardware-aware sizing (it detects the machine's memory, greys out models
that don't fit, labels the rest "recommended"). But on a 16 GB Mac it labelled **12-14B** models
"recommended" - the exact sizes that froze the box at ~15% free RAM (#413). The verdict was binary
(fits / doesn't fit) and checked only that the 4-bit weights fit the usable budget; it reserved no
headroom for KV/context growth, the app + WebView + Python backend, or a second (verifier) model.

## Requirements

- Classify each local model on this machine into three tiers, not two:
  - **recommended** - fits and leaves comfortable free memory to run well;
  - **runs, but tight** - fits but leaves little headroom (warn before running);
  - **needs more memory** - won't fit (not selectable).
- On a 16 GB Mac, an 8-9B is recommended; a 12B is "tight"; a 14B is "needs more memory".
- The recommended default suggestion is a model that runs *well* (comfortable), not the largest that
  merely fits. A machine too small for any comfortable model still gets a usable (tight) default, not
  nothing.
- Larger machines are unaffected: a 32 GB Mac still recommends a 32B, a 64 GB Mac a 70B.
- On a dedicated GPU there is no OS/app contention, so the tier stays binary (no "tight").

## How

`sizing.fit_tier(params_b, mem_gb, kind)` classifies by the free RAM a model leaves after its resident
footprint (weights + KV + runner working set): `recommended` if it leaves >= `_COMFORT_FREE_GB` (7 GB)
free, `tight` if it fits but below that, `too_large` otherwise. The picker view uses it to set each
model's `tight` flag and to pick the family default (largest comfortable, else largest that fits); both
picker templates render the "runs, but tight" warning.

## Acceptance criteria

- `fit_tier`: 16 GB -> 8/9B recommended, 12B tight, 14B too_large; 8 GB -> 3B tight; 32 GB -> 32B
  recommended; 64 GB -> 70B recommended.
- The picker marks Mistral Nemo 12B "tight" (not recommended) and defaults to Llama 3.1 8B on 16 GB; it
  still returns a (tight) default on 8 GB.
- Covered by `tests/test_model_picker_tiers.py`.
