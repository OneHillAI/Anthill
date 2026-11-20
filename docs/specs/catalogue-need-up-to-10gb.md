# Spec: a memory need for each catalogue model up to 10 GB, by the calibrated rule

Status: implemented (data change). Lane: `pillar:model`. Issue: #336. Builds on `docs/specs/catalogue-precomputed-need.md`
(#320: the app reads `need_gb`).

## Problem

No shipped row carries `need_gb`, so every model is sized by the flat formula (`params x 0.55 + 0.8 + 2.5`). Against the
measurements we have, it is 2.1 to 2.6 GB too strict for small models and slightly too lenient for a 9B model. The
maintainer decided on 2026-11-20 to use one rule for models whose library file is up to 10 GB, and to keep the formula for
everything larger until it is measured (#335).

## The rule

`need_gb = 1.3 x file size (GB) + 1.2 GB`, rounded to one decimal. File size is the library page size of the exact q4_K_M
build (Qwen3.5 0.8B has no q4_K_M: the top of its 1.2 to 1.3 GB page range is used). It is calibrated on five points with a
file of 9.1 GB or less: the memory a model occupied was 0.98 to 1.30 times its file size. The constant 1.2 GB is a
calibrated allowance, chosen so the 9B model with the embedder matches the roadmap's 9.8 GB (#97); it is NOT the embedder's
size (the measured bge-m3 resident size in `catalogue-precomputed-need.md` is 0.69 GB). The factor 1.3 is the highest ratio
seen, so on the calibration points the rule never under-states; it over-states by up to 1.8 GB. Two of the five points are
not our own runs (the 9B figure and the 14B figure), and the points come from different engines and builds.

## Requirements

1. Exactly these 13 local rows get `need_gb` and `need_basis`: `qwen3.5:0.8b` 2.9, `qwen3.5:2b` 3.7, `qwen3.5:4b` 5.5,
   `qwen3.5:9b` 9.8, `gemma4:e2b` 7.2, `gemma4:e4b` 9.8, `gemma4:12b` 11.6, `gemma3:1b` 2.2, `gemma3:4b` 5.6,
   `granite4.2:3b` 4.1, `granite4.2:8b` 8.1, `nemotron-3-nano:4b` 4.8, `phi4:14b` 13.0. No other key of any row changes.
2. Rows above 10 GB and cloud rows carry no `need_gb`; they are judged exactly as before.
3. The plain tag is what the row pulls; on the shipped engine (0.30.10) it is the GGUF build (not measured here), so the
   figure holds for the shipped engine only.

## What changes for users (by the app's own thresholds, Apple, one stream, 8k window)

- Fit labels: Qwen3.5 9B and Gemma 4 E4B become "tight" at 16 GB (need 9.8 GB leaves 6.2 GB free, under the 7 GB the app
  wants); Gemma 4 12B becomes "too large" at 16 GB (11.6 GB against 10.6 GB usable); Nemotron 3 Nano 4B becomes "tight" at
  8 GB (need 4.8 GB, under 66% of 8 GB). No other label changes at 8, 16, 24, 32 or 64 GB.
- Defaults: the 8 GB default and first-run suggestion change from Qwen3.5 2B to Nemotron 3 Nano 4B (a "tight" model; the rule
  puts a 4B inside the 66% line). At 16 GB the default (Qwen3.5 9B) and the first-run suggestion (Nemotron 3 Nano 4B) are
  unchanged; the Gemma family pick at 16 GB moves from the 12B to Gemma 4 E4B. 24, 32 and 64 GB: no change.
- This moves the 8 GB anchor of `local-model-sizing.md` ("at most a 3B") to a 4B. The measured 4B of that family (2.97 GB +
  the embedder) is smaller than the rule's figure, but a 4B on an 8 GB Mac leaves little for the system.

## Acceptance

- `tests/test_catalogue_need_up_to_10gb_336.py`: the 13 rows have the rule's value, no other row has a need, each need says
  where it comes from, the file loads, and the verdicts that change at 16 GB are exactly the three listed.
- `tests/test_catalogue_precomputed_need.py` still proves that every row without a need is judged as before and that the
  per-family picks differ from the flat formula only at 8 GB (Nemotron) and 16 GB (Gemma).
