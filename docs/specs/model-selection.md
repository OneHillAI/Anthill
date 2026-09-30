# Capability-aware model selection

## Problem

Model selection felt very limited, and worse, wrong. On a fresh install nothing is downloaded, so "what is
already on the box" tells a new user nothing. What they need is the opposite: *given this machine (or the
VPC / GPU I am about to provision), what models can I actually run, and let me pick one.* And the choice
must be the current **frontier**, ranked by intelligence - not a comfortable, dated shortlist.

A hand-typed catalog cannot do this. The open frontier moves monthly and is led by models that post-date
any fixed list (GLM-5.x, DeepSeek V4, Kimi K2.x, MiniMax M3, Qwen3.5/3.6, gpt-oss). A shortlist anchored on
Llama / Gemma / Mistral is stale on arrival.

## Approach: a refreshable, intelligence-ranked catalog

The catalog is **data, not code**: a bundled JSON (`hosting/model_catalog.json`) plus an in-app **Refresh**
that fetches the latest. Selection is capability-first on **both** surfaces - the local `/models` picker
(keyed to detected hardware) and the org / VPC choice (keyed to the chosen GPU tier).

- **Bundled + refreshable.** `load_catalog()` reads the bundled seed, then an override the Refresh writes
  to the Anthill home (`~/.anthill/model_catalog.json`); the last that parses wins, and a corrupt file
  falls back to the seed (never breaks the picker). `POST /models/refresh-catalog` fetches from
  `ANTHILL_MODEL_CATALOG_URL` (default `https://anthill.run/model-catalog.json`), validates, and writes the
  override. Consumers load the catalog at call time, so a Refresh takes effect without a restart. Between
  refreshes it is fully offline / local-first.
- **Intelligence x fit ranking.** Each model carries an `intelligence` index and `active_b` (MoE active
  params; `params_b` still drives memory). `recommend()` and the per-family picker now choose the
  **smartest model that fits**, not merely the largest - so a 35B-A3B MoE (35B resident, 3B active) can be
  preselected over a dense but duller model. Families are shown strongest-first; the default family tab is
  the strongest overall, origin-blind (the old non-Chinese default is retired).
- **The frontier.** The seed spans DeepSeek (V4-Pro/Flash, V3.1, R1), MiniMax (M3), GLM (5.1/5.2,
  4.7-flash), Kimi (K2.7), Qwen (3.6 35B-A3B, 3.5 ladder), gpt-oss (20B/120B), plus Llama/Gemma/Mistral/Phi
  in the tail. Ollama tags are verified against the live library; `hf_id`, param counts and intelligence
  are best-effort for the newest models and are corrected by Refresh.

The existing escape hatches remain: the `/models` "Advanced: custom tag" field and the org "Other" id.

## Data model

`hosting/model_catalog.json` rows: `name`, `family`, `origin`, `params_b` (total), `active_b`,
`ollama_tag`, `hf_id`, `context_k`, `license`, `intelligence`, `gated`, optional `quant_hf_id`. Loaded into
the `sizing.Model` dataclass (gained `intelligence`, `active_b`, `license`). No DB change.

## Single-GPU fit gate (the honest capability boundary)

**Requirement.** The org / VPC picker must only let an admin select a (model, GPU tier, precision) that a
single provisioned GPU can actually serve. It must not be possible to save a selection that would provision
a pod that then fails to load the weights: that spends real money for no server.

**Why single-GPU.** Provisioning serves one worker with no tensor-parallelism (`lambda_provision` /
`runpod_provision` size a single GPU), so the ceiling is one card's VRAM, not a cluster's. vLLM loads at
fp16 by default; a 4-bit (AWQ/GPTQ) build roughly triples the ceiling but only exists when the catalog row
carries a `quant_hf_id`. Without one, `source.servable_id` falls back to the fp16 repo, so the 4-bit
ceiling is irrelevant for that model.

**The predicate.** `sizing.servable_on_gpu(params_b, vram_gb, *, quantized, has_quant)` is the single
shared expression of that boundary: it composes the two existing ceilings (`cloud_gpu_max_params` for fp16,
`cloud_gpu_max_params_quantized` for 4-bit) with the `has_quant` condition, so callers never re-derive it.
It is a deliberate composition point, not a wrapper: both the browser picker's greying and the server-side
save gate consume it, and the coming (model x cloud) offering reuses it to only present combinations that
serve.

**Enforcement.** The browser picker greys out non-servable rows, but that is UI only and a direct form post
bypasses it, so `POST /settings/organization` re-checks server-side and refuses a non-servable selection
with `error=too_big` before persisting anything. The quant flag is normalised the same way in both places:
4-bit is honoured only for a model that actually has a quant build.

**Scope / trust boundary (intentional skips).**
- **On-prem** is the org's own box of unknown VRAM, so the gate does not apply (consistent with the picker
  keeping the full list selectable for on-prem).
- **Custom / "Other" ids** are the deliberate escape hatch for an admin who knows what they are pointing at.
  A typed repo id cannot be sized honestly: the same name may be an fp16 or a 4-bit build, so gating it on a
  name-parsed size would falsely reject legitimate quantized repos. This matches the catalog trust boundary
  (a human typing an id is an unrestricted human decision; only wire-supplied catalog rows are gated). The
  gate therefore applies only to known catalog rows, whose size and quant availability are vetted.

**Acceptance criteria.**
- A catalog model above the chosen tier's fp16 ceiling with no quant build is refused (`error=too_big`),
  server-side, even via a direct POST.
- A model whose size is within the 4-bit ceiling but which has no quant build is judged at fp16 and refused
  (no phantom quant path).
- A model with a real quant build is accepted at 4-bit exactly when it fits the tier's 4-bit ceiling, and
  refused when the tier is too small even at 4-bit.
- A model within the fp16 ceiling is accepted. On-prem and custom ids are never blocked by this gate.

**Out of scope (phased).** Frontier giants that need multi-GPU serving (GLM-5.x, Kimi K2.x, DeepSeek
V3.1/V4, MiniMax M3, Qwen3.5 122B, gpt-oss 120B) stay unoffered until multi-GPU serving exists; phase 1 is
the ~70B-class envelope one GPU can serve.

## Disk-space capacity (Phase 6a of the local-vs-frontier capability roadmap)

Fit-gating so far only checked RAM/VRAM (`onprem_council_fits`, `servable_on_gpu`) and speed
(`is_usable_speed`) - never disk. A model must be downloaded and stored before it can run at all, so disk
space is a third, independent precondition, not a detail. `sizing.free_disk_gb()` is a best-effort,
fail-open probe (mirrors `free_mem_gb()`'s None-on-failure contract; reuses the existing
`anthill.backup.ollama_models_dir()` OLLAMA_MODELS-aware path rather than re-deriving it) and
`sizing.onprem_council_fits_on_disk()` sums the on-disk size of a proposed set of on-prem models
(`family_download_gb()`, already used for the picker's displayed download size) against free space, with
an explicit comfort headroom (`_DISK_COMFORT_FREE_GB`) for OS/transient-download overhead - the disk
equivalent of `onprem_council_fits`'s memory check. Deliberately a separate function rather than folded
into the existing memory gate, so neither existing callers nor their tests change. Consumed by the unified
onboarding flow (a separate, later change) alongside the memory and speed gates, not wired into any UI here.

## Follow-ups

- **Done.** The hosted catalog is published at `anthill.run/model-catalog.json` and refreshed daily.
  `scripts/gen_model_catalog.py` generates it from the seed - intelligence scores are curated editorial
  values in the seed (no external API; an Artificial Analysis integration exists but is off by default,
  since its free Data API forbids redistribution and this catalog is public), plus installable-tag checks
  against the Ollama registry, param/licence/gated cross-checks against HuggingFace, and newly trending
  frontier models flagged for review (never auto-added). It is served from `www/` via Cloudflare Pages and
  republished by `.github/workflows/refresh-model-catalog.yml`; hosting setup is in `www/README.md`.
- Show `active_b` / context and a short "good for" note in the picker; surface intelligence in the org select.
- Verify the newest `hf_id`s for the cloud path as the labs publish canonical repos.
