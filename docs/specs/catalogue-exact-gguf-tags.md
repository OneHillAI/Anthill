# Spec: the ambiguous catalogue rows name the exact GGUF build, old plain tags kept working

Status: implemented. Lane: `pillar:model`. Issue: #345. Step 1 of the engine update (#264). Builds on
`docs/specs/catalogue-replaced-tags.md` (#325: a row's `replaces`).

## Problem

On Ollama 0.40.x a plain tag such as `qwen3.5:4b` pulls the MLX build for an MLX-capable architecture, and that build's memory
grew from 4.07 GB to 12.59 GB over 48 chat turns on a 16 GB Mac (Ollama issue 16698); the exact GGUF tag `qwen3.5:4b-q4_K_M`
stayed flat at 3.38 GB. The shipped engine (0.30.10) pulls GGUF for the plain tag. Before the engine moves to 0.40.16, the 13
rows whose plain tag is ambiguous (the library page shows a size range for it and its digest matches no exact tag) must name the
exact GGUF build, and installs that chose the plain tag must keep working.

## Requirements

1. Each of these rows has `ollama_tag` = the exact GGUF tag and `replaces` = [the old plain tag]; every other key of the row is
   unchanged:
   `qwen3.5:0.8b` to `qwen3.5:0.8b-q8_0` (the only GGUF tag listed), `qwen3.5:2b` to `qwen3.5:2b-q4_K_M`, `qwen3.5:4b` to
   `qwen3.5:4b-q4_K_M`, `qwen3.5:9b` to `qwen3.5:9b-q4_K_M`, `qwen3.5:27b` to `qwen3.5:27b-q4_K_M`, `qwen3.6` to
   `qwen3.6:35b-a3b-q4_K_M`, `gemma4:e2b` to `gemma4:e2b-it-q4_K_M`, `gemma4:e4b` to `gemma4:e4b-it-q4_K_M`, `gemma4:12b` to
   `gemma4:12b-it-q4_K_M`, `gemma4:26b` to `gemma4:26b-a4b-it-q4_K_M`, `gemma4:31b` to `gemma4:31b-it-q4_K_M`,
   `nemotron-3-nano:30b` to `nemotron-3-nano:30b-a3b-q4_K_M`, `nemotron-3-nano:4b` to `nemotron-3-nano:4b-q4_K_M`.
2. Rows whose plain tag is the same file as an exact tag (equal digest) and the two gpt-oss rows (one tag only) are not touched.
3. Every exact tag was accepted by both engines at the pull handshake (a pull started on a private instance of 0.30.10 and of
   0.40.16, cancelled after the first layer began, 2026-11-20), so no row gets `min_engine_version`.
4. An install that chose a replaced plain tag keeps working everywhere a model is matched, including the Settings model picker:
   a saved model named by an old plain tag is shown under its row's exact tag (pre-selected, never offered for uninstall while
   active), the Uninstall button of an installed row removes the tag that is actually on disk, and the Models page does not call
   such a model "custom" (`_model_picker_view`, `/personalize` and `/models` in `anthill/web/app.py`, `_council_builder.html`).
5. No change to the engine pin, the pull path, sizing or any `need_gb`.

## What is not claimed

- A handshake is not proof that the model loads or runs. The exact build was measured flat in memory only for
  `qwen3.5:4b-q4_K_M`; for the other rows that is an assumption (#335).
- For the ambiguous rows it is not known whether an existing install under the old plain tag holds the same file as the exact tag.

## Acceptance

- `tests/test_catalogue_exact_gguf_tags_345.py` and the updated catalogue tests pass: the 13 switches and nothing else, every new
  tag safe, no `min_engine_version`, every old plain tag resolves to its row and shows as installed, the `need_gb` of each row
  kept, the Settings picker marking an active old-tag model and uninstalling the tag on disk, the Models page not treating it as
  custom. Without the data change four of its seven tests fail; with the data but without the page fix two fail.
