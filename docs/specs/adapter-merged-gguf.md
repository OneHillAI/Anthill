# Spec: a trained adapter is merged into its base and registered as a GGUF, which every Ollama accepts

Status: implemented. Lane: `pillar:model`. Issue: #273. Builds on `docs/specs/adapter-registration-ollama.md` (the refusal
is named). Related: #264 (the engine update).

## Problem

Ollama 0.34.1 and later refuse `ollama create ... ADAPTER`, so a LoRA adapter trained for an organisation (the non-MLX
path: a GPU machine trains, the org machine serves through Ollama) cannot be registered on the new engine (0.40.16). Tested
on 2026-11-24 with a real tiny adapter, on private copies of Ollama 0.30.10 and 0.40.16:

- `ADAPTER` with the PEFT folder exactly as the app writes it (`adapter_model.safetensors`, `README.md`, `checkpoint-60`)
  fails even on 0.30.10 ("no Modelfile or safetensors files found"); with the weights renamed to `model.safetensors` it
  works on 0.30.10 for a Llama-architecture base (the answer changed) and fails with "unsupported architecture" for a Qwen
  base; it is refused on 0.40.16.
- Merging into the base and importing the safetensors folder works on 0.30.10 but is refused on 0.40.16 for a Qwen2 base
  ("unsupported MLX architecture"; on 0.40.x a safetensors import takes the MLX path, not GGUF).
- Merging, converting to GGUF with llama.cpp's converter and registering the file works on both engines, runs on the llama.cpp
  runner on 0.40.16 and carries the adapter's effect.

## Requirements

1. After a PEFT run, when the training image configures the converter (`ANTHILL_GGUF_CONVERTER`), `train_adapter` merges the
   adapter into its base (the Hugging Face repo the training used, in bfloat16) and converts it with llama.cpp's
   `convert_hf_to_gguf.py` to `merged.gguf` inside the adapter folder (`--outtype` from `ANTHILL_GGUF_OUTTYPE`, default
   `q8_0`). It runs where the base weights and torch are (the training image). Every backend already fetches the adapter
   folder, so the file travels with it. A failed merge never loses the adapter: a warning is printed and the adapter is kept.
2. `register_with_ollama`, when the folder holds `merged.gguf`, registers it on any Ollama version: it reads the base model's
   Modelfile from the Ollama that `ollama create` talks to, drops its `FROM` and `ADAPTER` lines, writes `FROM <absolute
   path of merged.gguf>` plus the rest (chat template, stop words, parameters) and runs `ollama create`. Without the file the
   `ADAPTER` route is unchanged and a refusal is named (`ADAPTERS_REFUSED_NOTE`, now also saying that the training machine
   sent no merged file).
3. The eval gate and the rule that a rejected candidate never replaces the live model are untouched (`promote_if_better`).
4. The early stop of #276 stays in `execute_run`: a run on an Ollama that refuses adapters is stopped before any compute is spent,
   unless the owner declares that the rebuilt training image writes `merged.gguf` (`ANTHILL_TRAINER_MERGES_GGUF=1` on the org
   machine, `trainer.trainer_merges_gguf()`); the org machine cannot see the image. The owner rebuilds the image from
   `docker/Dockerfile.trainer` and sets the flag; until then nothing changes for a user.
5. `docker/Dockerfile.trainer` installs `gguf` and `sentencepiece`, downloads the converter pinned to llama.cpp tag `b6000`
   and checks its SHA-256 (one release file; the current master version needs a whole package), and sets
   `ANTHILL_GGUF_CONVERTER`.

## Verified (2026-11-24 and 2026-11-25, real engines)

- The app's own `train_adapter` (PEFT, Qwen2.5-0.5B-Instruct, Apple MPS) wrote `merged.gguf` (531 MB, q8_0) next to the adapter.
- The app's own `register_with_ollama` registered it on Ollama 0.30.10 and on 0.40.16 (each client against its own server):
  `ollama show` reports format gguf, Q8_0, and on 0.40.16 the llama.cpp runner; the base's chat template is kept; the model
  answers ("The capital of France is Paris."). A first run found that a relative path made Ollama read `FROM e2e/...` as a
  registry name; the path is now made absolute.
- Tests: `tests/test_adapter_merged_gguf_273.py` (13) and the updated `tests/test_adapter_registration_273.py`; they fail
  without the change.

## Not shown

- That the adapter taught an exact fact (the answers changed but did not become the trained sentence); the eval gate on a
  produced model; the Docker image build and the merge on a real GPU host (the image is built and validated by the owner).
- Bases other than Qwen2 and Llama-family models (Qwen3.5, Qwen3.6 and Gemma 4 on 0.40.16 were not tried); quantising below
  q8_0 (a q8_0 GGUF of an 8B model is about 8.5 GB and travels back from the GPU machine; `f16` is about 16 GB).
- Serving the adapter behind an OpenAI-compatible endpoint (route b): not tried.

## Acceptance

- `tests/test_adapter_merged_gguf_273.py` passes: the merge and conversion command, the temporary folder removed, plain errors
  for a missing converter, a failing converter and missing libraries, the converter configured or not, a failed merge keeping
  the adapter, MLX never merging, the registration with the base template and without `ADAPTER`, an absolute path, the
  `ADAPTER` fallback, plain errors for an unreadable base and a failed creation, the eval gate still rejecting and removing a
  losing model, and the Dockerfile pinning the converter by tag and checksum.
- The existing training tests pass; the full preflight passes.
