# Spec: local vision-model selection (licence-clean, non-Chinese default)

Status: proposed
Lane: `pillar:model`
Relates to: `anthill/routing/router.py` (the vision preference + licence block),
`anthill/web/app.py` (first-run auto-pull + settings), `anthill/web/db.py` (`OrgSettings`),
`docs/specs/asdd-model-roster.md` (the same no-Chinese-origin stance for the ASDD roster).

## 1. Problem

Anthill reads scanned and image-only PDFs during ingestion using a LOCAL Ollama vision model. The
first-run flow auto-pulled and the router preferred `qwen2.5vl:3b`. That model is published under the
Qwen RESEARCH LICENSE, which permits non-commercial use only. Shipping it as the default vision model
of a commercial, AGPL-licensed product is a real licence exposure: every commercial deployment that
ingested an image PDF was running inference on a research-only model.

Two concerns, in priority order:

1. Licence. `qwen2.5vl:3b` (non-commercial) must not be pulled, preferred, or served by default.
2. Sovereignty. The founder wants the default fully-local vision stack to be licence-clean and
   100% non-Chinese-origin. Chinese-origin vision models (the Qwen families) must not appear in the
   default preference at all; Qwen3-VL is reachable only through an explicit, off-by-default opt-in.

Note that the licence line is not "Chinese vs not": `qwen2.5vl:7b` is Apache-2.0 (usable), while
`qwen2.5vl:3b` is research-only. Both concerns hold at once here: the default is Apache-2.0 AND
non-Chinese.

## 2. Requirements

- THE SYSTEM SHALL default to a fully-local, licence-clean, non-Chinese-origin tiered vision stack:
  IBM Granite vision (`granite3.2-vision:2b`, ~2.4 GB, Apache-2.0, US-origin) as the small
  out-of-the-box default, and Mistral Small 3.2 (`mistral-small3.2:24b`, ~15 GB, Apache-2.0,
  EU-origin) as the higher-quality tier.
- THE SYSTEM SHALL NOT include `qwen2.5vl:3b` in the model catalogue or in any routing preference, and
  the router SHALL NOT return it for any task even if a user has pulled it manually (a hard licence
  block, so the same-family and vision-capability fallbacks cannot re-select it).
- THE default VISION preference and the vision entry of the default DOCUMENT preference SHALL contain
  only non-Chinese, licence-clean models. No Chinese-origin model SHALL appear in any default
  preference. (Microsoft `phi-4-multimodal` was evaluated as a third non-Chinese fallback but has no
  official Ollama library tag, so the default preference is `[granite3.2-vision:2b,
  mistral-small3.2:24b]` with no further fallback.)
- THE SYSTEM SHALL make Qwen3-VL (`qwen3-vl:8b` / `qwen3-vl:4b`) available only through an
  off-by-default "maximum accuracy" opt-in (`OrgSettings.vision_max_accuracy`). When that opt-in is
  off, the router SHALL NOT prefer Qwen3-VL.
- WHEN the "maximum accuracy" opt-in is on, THE SYSTEM SHALL prefer Qwen3-VL first for VISION and
  DOCUMENT tasks, falling through to the licence-clean non-Chinese default when Qwen3-VL is not
  installed.
- THE "maximum accuracy" opt-in SHALL apply to the document INGESTION path (scanned-PDF and image OCR),
  not only interactive chat: the web upload handlers SHALL pass the account's
  `OrgSettings.vision_max_accuracy` into `ingest(...)`, which threads it to the vision router.
- THE SYSTEM SHALL auto-pull the small non-Chinese default (`granite3.2-vision:2b`) on first run when
  vision auto-pull is enabled; WHEN the "maximum accuracy" opt-in is on it SHALL auto-pull Qwen3-VL
  (`qwen3-vl:8b`) instead.
- THE opt-in SHALL default to off, so a deployment that does nothing keeps the sovereign non-Chinese
  default.

## 3. Design notes

- The router carries a `vision_max_accuracy` flag (constructor argument plus an
  `ANTHILL_VISION_MAX_ACCURACY` environment override, mirroring the existing `ANTHILL_FORCE_MODEL`
  pattern). The chat path and the web upload handlers pass the account's
  `OrgSettings.vision_max_accuracy`; it is threaded through `ingest()` ->
  `_source_content_to_page()` -> `_ingest_with_vision()` / `_vision_backend_config()` -> `TaskRouter`.
  The environment lever covers callers with no OrgSettings (the CLI).
- `qwen2.5vl:7b` (Apache-2.0) and `qwen3.5:9b` are retained in the catalogue only; neither is in any
  default preference. They are reachable solely as a last-resort vision-capability fallback if a user
  has manually pulled one and no clean model is installed.
- The new `OrgSettings.vision_max_accuracy` Boolean column is added via the existing ensure-columns
  migrator, so no manual migration is required.

## 4. Acceptance criteria

- `qwen2.5vl:3b` is absent from the catalogue and every preference, and `TaskRouter.pick(VISION)`
  never returns it, including when it is the only installed vision model (returns the fallback tag
  instead).
- With the opt-in off, the default VISION preference contains only non-Chinese, licence-clean models
  (no Qwen family), and with Granite installed the router picks `granite3.2-vision:2b`.
- With the opt-in on and Qwen3-VL installed, the router picks `qwen3-vl:8b` for VISION and DOCUMENT,
  including via the ingestion path (a scanned-PDF upload from an opted-in account).
- First-run auto-pull fetches `granite3.2-vision:2b` by default and `qwen3-vl:8b` when the opt-in is
  on.
- The install hint and CLI help name the new default (`granite3.2-vision:2b`).
