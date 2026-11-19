# Spec: model catalogue release 1 (backfilled rows, explicit GGUF tags, read licences, scores unscored)

Status: data change, no code. Lane: `pillar:model`. Issue: #304. Builds on `docs/specs/model-catalog-trust.md` (signed
catalogue, shape gate) and `docs/specs/huggingface-source-pinned.md` (the later second source). The file is
`anthill/hosting/model_catalog.json`; the signing workflow (`refresh-model-catalog.yml`) publishes it unchanged.

## Problem

1. The 43 `intelligence` values have no recorded source. Against the Artificial Analysis Intelligence Index (the only
   independent index found) 15 of the 43 models are listed, none of the values match (the seed is higher by 17 to 56
   points), and that site's terms do not allow scripted reading or publishing its numbers in a public file.
2. 13 rows use a plain tag that Ollama 0.40.x serves as an MLX build. On a 16 GB Mac its memory grew from 4 GB to
   12.6 GB over 48 turns, while the GGUF build (`qwen3.5:4b-q4_K_M`) stayed flat at 3.4 GB. This blocks the engine update
   (issue #264).
3. Rows carry no licence terms, no verified region and no precomputed memory fit. 17 licence labels were never read and
   three were wrong (Nemotron 3.5 is OpenMDW-1.1, not "NVIDIA Open Model"; Kimi K3 is its own licence, not Modified MIT;
   Llama is two named licences).

## Decisions already taken (maintainer, 2026-11-19)

- Licences are shown with restriction badges; nothing is hidden by licence.
- Origin is a region (ten regions), not a country; a region is `region_verified` only from the lab's own page.
- A score appears only with a source; otherwise `unscored`. No Artificial Analysis number is used.
- GGUF is the default build. An MLX build is an advanced option only if it passes the memory test.
- The catalogue carries no speed, only memory fit. No test downloads for now, so untested rows are `unvalidated`.
- A person approves the file; signing is the existing workflow.

## Requirements

1. Every existing key keeps its meaning and value, except `intelligence` (0 for all 43) and the licence strings that
   were wrong (`license` of Kimi K3, Nemotron 3.5 x3, Nemotron 3 x3, Llama x3, Gemma 3 x3, Kimi K2.7 Code unchanged name).
2. `intelligence` stays a JSON number (the app converts it with `float()`); the structured value is
   `score: {"status": "unscored", ...}`. Because every score is 0 the picker offers the largest model that fits.
3. `ollama_tag` is unchanged on every row. The 12 explicit GGUF tags are in the new key `gguf_tag` (and
   `source.gguf_ref`); the app switches to them in a later release. `qwen3.6` has none: the mapping is unconfirmed.
4. New additive keys per row: `licence` (spdx, name, link, restrictions[], needs_human_read, read_by, legal_reading),
   `origin_region`, `region_verified` (false on all 43), `origin_basis`, `cloud`, `kind`, `maturity`, `maintained`,
   `validation` (`unvalidated`, except `qwen3.5:4b-q4_K_M` which has a recorded 48-turn memory test),
   `build`, `build_checked` (library-page method, or null), and for local rows `size` and `fit`.
5. `fit` has the tiers 8, 16, 24, 32 and 64 GB with `fits`, `tight`, `too_large` or `not_computed`. Rule: need =
   weights + context memory at 8k + 1.0 GB; budget = 0.67 x memory up to 32 GB and 0.75 x at 64 GB; `fits` if need is at
   most 0.9 x budget, `tight` if at most the budget. Layers, KV heads and head size come from the published `config.json`
   (35 reads); the six gated repositories (Llama x3, Gemma 3 x3) are `not_computed`. Weights are the library page size
   for the 13 checked rows and an estimate (parameters x 4.85 bits) for the rest, labelled in `size.weights_basis`.
6. Cloud rows (`:cloud`) have `cloud: true` and no size or fit.
7. Nothing unverified is presented as fact: unknowns are labelled (`unknown`, `not_computed`, `unvalidated`,
   `estimated_from_params_q4`).

## What the user notices

Because every score is 0, the picker's default no longer comes from a score. It comes from the `fit` table in each row
(issue #311, `docs/specs/picker-order-by-fit-table.md`): the largest model marked `fits` for the machine's memory tier. The
defaults: 8 GB Qwen3.5 4B, 16 GB Qwen3.5 9B, 24 GB gpt-oss 20B, 32 GB Nemotron 3 Nano 30B, 64 GB Qwen3.6 35B-A3B.

## Licence reading (our reading, not legal advice; full text in the maintainer's notes)

Local pull, own machines, company use and local fine-tunes are fine under all read licences. Duties arise when weights or
fine-tunes are given to another person: Llama (copy of the agreement, a Notice file, "Built with Llama", a shared derived
model's name starts with "Llama"); Gemma Terms of Use (terms copy, Notice file, modified notices, pass on the Prohibited Use
Policy, Google may restrict use remotely); NVIDIA Nemotron Open Model License (licence copy and notices; indemnity to NVIDIA;
export laws); OpenMDW-1.1 (licence copy and notices); Kimi (notice kept; model-name display above 100M users or USD 20M
monthly revenue). Meta's Acceptable Use Policy was not read.

## Unchanged

`is_safe_ollama_tag`, `is_safe_row`, `load_catalog`, the signing workflow, the generator and the gates; the shape of old keys.

## Acceptance

- The file loads in the current app code; every row passes `is_safe_row`; every `intelligence` is the number 0.
- The 12 `gguf_tag` values are the expected explicit tags and are safe library tags; `ollama_tag` is unchanged for all rows.
- Local rows have a five-tier `fit` (values from the allowed set); cloud rows have none.
- No string from Artificial Analysis appears in the file.
- After merge the signed file verifies in the app and the picker still offers models on 8, 16, 24 and 32 GB machines.
