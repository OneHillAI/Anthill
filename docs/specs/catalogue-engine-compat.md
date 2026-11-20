# Spec: a catalogue row says which engine it needs; the local picker hides rows the bundled engine cannot run

Status: implemented (second slice of the catalogue support layer). Lane: `pillar:model`. Issue: #322. Builds on
`docs/specs/catalogue-precomputed-need.md` (#320) and follows `docs/specs/catalogue-bundled-engine-safe.md` (#318).

## Problem

Four catalogue rows the shipped engine (Ollama 0.30.10) cannot download reached users, and the first-run suggestion for 16
and 24 GB Macs pointed at one of them (#318, removed by the data fix #319). Nothing in the app prevents it happening
again: the catalogue is published from outside the app, an admin can refresh it into an old install, and the engine an
install runs is fixed by its release.

The rule: the picker lists only what can run, and the user never sees anything about engine versions.

## Requirements

1. A catalogue row may carry `min_engine_version`, the oldest Ollama that can download and run it, written as `MAJOR.MINOR`
   or `MAJOR.MINOR.PATCH` (a leading `v` is accepted). A value that is not a version is ignored: the row is then treated as
   stating no requirement. It never raises and never hides a row.
2. The app compares it with the engine it ships: the pin `_OLLAMA_VERSION` in `anthill/inference/ollama.py`, read without
   touching the network. `sizing.runs_on_bundled_engine(model)` is False only when the row names a newer version.
3. A row that does not run on the bundled engine is not offered in the local picker: `recommend_by_family` (so the
   first-run suggestion), the picker's family lists, the setup form's accepted choices and the local model options.
4. Not affected: the cloud and organisation sizing (`recommend` with a GPU box: a vLLM server does not use the bundled
   Ollama), a model that is already installed, and the Advanced custom-tag box (the person's own explicit choice).
5. No new text anywhere. A row that needs a newer engine is not listed; nothing about versions is shown.
6. No shipped row has the field yet, so every current pick, suggestion and list is unchanged.

## How the catalogue owner fills it

Set `min_engine_version` to the lowest Ollama version on which the row was tested to download and answer a chat turn. A
row with no recorded test should say nothing (no requirement) only if it has been tested on the bundled engine. Measured
so far: the four rows removed in #319 (`qwen3.8:27b`, `nemotron-3.5-bolt:e4b`, `nemotron-3.5-lightning:30b`,
`nemotron-3.5-super:120b`) are refused by 0.30.10 and accepted by 0.40.16 at the download step, and where between those
two versions they started to be accepted was not found out, so the safe value for them is `0.40.16` until it is. A
download being accepted is not proof that the model loads: test the load too. When the bundled engine is updated, rows
whose requirement it now meets appear without any catalogue change.

## Limits

- The comparison is with the engine the app ships, not with the Ollama that is running: a person who runs their own newer
  Ollama, or a Linux install (which has no bundled engine), does not see a row that needs more than the pin. That is the
  conservative side. If the Windows pin is ever split from the macOS one, this reads the shared pin.
- It does not make a failed download say anything; a row that needs a newer engine is simply absent.

## Acceptance

- `tests/test_catalogue_engine_compat.py` passes: versions are read and bad ones ignored; only a row naming a newer engine
  than the shipped one is hidden; a hidden row is never picked or suggested at any memory tier; the cloud sizing is not
  filtered; the setup picker page and the local model options leave it out; and for every shipped row and tier the picks and
  the first-run suggestion are the same as before.
- The neighbouring catalogue, sizing, picker and guard tests pass; the full preflight passes.
