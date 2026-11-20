# Spec: the catalogue offers only models the bundled Ollama can download

Status: implemented. Lane: `pillar:model`. Issue: #318 (replaces PR #310, closed at the maintainer's instruction on
2026-11-20). Relates to #264 (the engine update, held).

## Problem

The released app (v1.1.1 and v1.2.0-rc.1) bundles Ollama 0.30.10 and ships a 43-row catalogue. Four of the rows are
models that engine cannot download: `qwen3.8:27b`, `nemotron-3.5-bolt:e4b`, `nemotron-3.5-lightning:30b` and
`nemotron-3.5-super:120b`. A pull of any of them on 0.30.10 is refused by the registry at the manifest step with HTTP
412 ("requires a newer version of Ollama"). Ollama 0.40.16 accepts all four.

Found by starting a pull of every non-cloud catalogue tag on a real 0.30.10 and cancelling once the registry answered
(about 7 MB downloaded in all): 31 of 35 accepted, these 4 refused. Run with v1.1.1's own picker code and catalogue, the
first-run suggestion for a 16 GB and a 24 GB Mac is `nemotron-3.5-bolt:e4b`, one of the four. The pull then fails and,
read from the code, shows the user nothing.

The rule: the picker lists only what can run, and a user never sees anything about engine versions. So a model the
shipped engine cannot run must not appear.

## Requirements

1. The four rows are removed from `anthill/hosting/model_catalog.json` (43 rows become 39). No other row, field,
   score, default or picker rule changes.
2. While the bundled engine is older than 0.40.0, no catalogue row is one of the four, and for every memory tier (8, 16,
   24, 32 and 64 GB, Apple Silicon) neither `recommend`, `recommend_by_family` nor `suggest_local_setup` returns one.
3. The removed rows are recorded below so they can come back.

## The removed rows (to return when the bundled engine is updated and each is tested on it)

| Name | Tag | Origin |
|---|---|---|
| Qwen3.8 27B | `qwen3.8:27b` | Alibaba, China |
| Nemotron 3.5 Bolt | `nemotron-3.5-bolt:e4b` | NVIDIA, US |
| Nemotron 3.5 Lightning | `nemotron-3.5-lightning:30b` | NVIDIA, US |
| Nemotron 3.5 Super | `nemotron-3.5-super:120b` | NVIDIA, US |

The full rows are in the git history of the catalogue file. Returning them needs: the bundled engine updated (#264),
each row pulled and answered a chat turn on it, and, for an MLX-capable architecture on Ollama 0.40.x, a GGUF build
named in the row (a plain tag there pulls an MLX build whose memory grew from 4.07 GB to 12.59 GB over 48 chat turns on
a 16 GB Mac).

## Effect of the change (run with v1.1.1's own picker code)

| Memory | First-run suggestion before | After |
|---|---|---|
| 16 GB, 24 GB | `nemotron-3.5-bolt:e4b` (refused) | `nemotron-3-nano:4b` |
| 8 GB, 32 GB, 64 GB | unchanged | unchanged |

Verified on 2026-11-20: `nemotron-3-nano:4b` (the replacement suggestion for 16 and 24 GB) was downloaded (2.84 GB, 75 s) on a real Ollama 0.30.10, the engine the app bundles, and answered chat turns correctly: Q4_K_M, family nemotron_h, 2.97 GB resident at an 8,192 window, cold load 1.4 s, writing 26 to 31 tok/s, reading about 296 tok/s on a 2,007-token prompt (the first word of that prompt took about 6.8 s). One M4 16 GB, three turns; a long-session memory test was not run.

## Delivery

Merging publishes the corrected file through the signing workflow. An install gets it only when an admin refreshes
the catalogue or with an app release. No release is planned, so installs that do neither keep the four rows.

## Not changed here

The shipped 8 GB first-run suggestion is a three-model council (a separate matter, app code); a failed download still
shows no message; the engine pin; the picker's defaults and scores.

## Acceptance

- `tests/test_catalogue_bundled_engine_safe.py` passes: the four tags are absent, no tier's recommendation or first-run
  suggestion is one of them, and the catalogue has 39 rows.
- The existing catalogue, sizing and picker tests pass; the full preflight passes.
- Verified before merge (with the maintainer's go for the download): `nemotron-3-nano:4b` downloaded and answered on the bundled 0.30.10, see above.
