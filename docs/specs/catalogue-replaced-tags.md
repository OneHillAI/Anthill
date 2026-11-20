# Spec: a catalogue row can name the exact build it pulls, and the older plain tags it replaces

Status: implemented. Lane: `pillar:model`. Issue: #324. Follows #320 (precomputed need) and #322 (engine requirement);
precedes the engine update (#264). Builds on `docs/specs/catalogue-precomputed-need.md` and
`docs/specs/catalogue-engine-compat.md`.

## Problem

A catalogue row names a model by a plain Ollama tag (`qwen3.5:4b`). Which build that tag downloads is decided by the
engine, not by the catalogue: Ollama 0.40.x pulls an MLX build for an MLX-capable architecture on a fresh plain-tag
pull, and its memory grows without a limit, while the GGUF build stays flat; the shipped engine (0.30.10) pulls GGUF.
So the same row can mean two memory costs, and a measured `need_gb` per row is true for one build only. A row must be able
to name the exact build it means (`qwen3.5:4b-q4_K_M`) without breaking installs that already chose the plain tag.

## Requirements

1. A row may list `replaces`: older tags it stands for. Only valid, distinct tags other than the row's own are kept;
   anything else (not a list, an unsafe or malformed tag) is ignored. It never raises and never hides a row.
2. Every place that matches a tag against a row treats a replaced tag like the row's own: the "installed" flag in the
   picker and the recommendation, the catalogue duplicate check that hides installed models already listed, the setup
   form's accepted choices, and the lookup of a model's row for sizing and the pull guard (`row_for_tag`, `row_tags`,
   `row_installed_tag` in `anthill/hosting/sizing.py`).
3. A model installed under a replaced tag is never offered for download again and is not listed twice: choosing the row
   (or its old tag) while the old tag is installed uses the installed model; choosing a replaced tag that is not installed
   pulls the row's own exact build tag, never the plain tag.
4. Exact build tags (`-q4_K_M` and similar) pass the existing tag safety check and are handed to `ollama pull` unchanged.
5. Two rows must not claim one tag. A row's own tag is never taken from it; a replaced tag that is another row's own tag,
   or was already claimed by an earlier row, is dropped from the later row (the first claim wins), when the catalogue loads.
6. Nothing in the pull path, the engine pin, the fit calculation or the scores changes. No new user-facing text.

## How the catalogue owner names a build

Put the exact build tag in `ollama_tag` and the plain tag the install may have chosen in `replaces`:
`{"ollama_tag": "qwen3.5:4b-q4_K_M", "replaces": ["qwen3.5:4b"]}`. Which rows move first is the catalogue owner's data change
after this: the twelve rows whose plain tag a 0.40.x engine turns into an MLX build (Qwen3.5 0.8B to 122B, Qwen3.8 27B, Gemma 4
E2B to 31B), each with its measured `need_gb` for that exact build.

## Acceptance

- With the shipped catalogue no row replaces a tag, and every pick, suggestion, family pick and "installed" flag is
  identical with the field present or stripped, at 8, 16, 24, 32, 64 and 128 GB (`tests/test_catalogue_replaced_tags_324.py`).
- With a test row whose tag is an exact build and which replaces a plain tag: an install pinned to the plain tag shows as
  installed, is accepted by the setup form, resolves to the row for sizing, and is not listed twice.
- A malformed replacement list is ignored without error. The new tests fail without the source change and pass with it.
