# Spec: a trained adapter that Ollama will not register is reported before it is trained

Status: implemented (the reporting part); the replacement route is open. Lane: `pillar:model`. Issue: #273. Related:
#264 (the bundled engine update), `docs/specs/ollama-engine-update-macos.md`.

## Problem

After a backend trains a LoRA adapter, Anthill registers it into the local Ollama with `ollama create <name> -f
<Modelfile>` (`FROM <base>`, `ADAPTER <dir>`; `register_with_ollama` in `anthill/training/trainer.py`, run by
`promote_if_better` for every training run that is not the Apple Silicon local path). Ollama removed that in v0.34.1:
`ollama create` with an adapter answers "LoRA adapters are no longer supported" (`errAdaptersUnsupported` in
`cmd/cmd.go` and `server/create.go`). Read in the source at v0.34.0 (absent), v0.34.1, v0.35.0, v0.40.0, v0.40.7 and
v0.40.16 (present); there are no releases 0.36 to 0.39. Checked on a real engine: Ollama 0.40.16 answers exactly that
for a PEFT-format adapter folder.

Three things went wrong for a user:

1. The refusal came after the training, possibly on a rented GPU, and was shown as a generic `ollama create ... failed`.
2. The command was the bare name `ollama`, found on `PATH`. The bundled and first-run engines are not on `PATH`
   (`find_ollama_bin` exists for that reason), so the call could crash with `No such file or directory: 'ollama'`
   before it reached the adapter question.
3. It already fails on any Ollama from 0.34.1, including a maintainer's own 0.40.7 and any organisation's own server.
   The bundled engine is 0.30.10 today, which accepts adapters in its server code (not shown by a test).

Not affected: the Apple Silicon local path (`_promote_and_serve_local_mlx`, an mlx-lm server, no Ollama).

## Requirements

1. `trainer.local_ollama_accepts_adapters()` reads `/api/version` of the server `ollama create` will talk to
   (`OLLAMA_HOST`, else `127.0.0.1:11434`) and returns False from 0.34.1 on, True before, and None when the version
   cannot be read. It never raises.
2. `executor.execute_run`, on the non-MLX path, stops before the training backend is called when the answer is False:
   the run is recorded as `failed` with the note "not started: ..." (`ADAPTERS_REFUSED_NOTE`), the account's training
   status goes back to idle, and no training compute is spent. True or None runs as before. The Apple Silicon local
   path never asks.
3. `register_with_ollama` raises `TrainerError(ADAPTERS_REFUSED_NOTE)` when Ollama answers that adapters are no longer
   supported, and keeps the existing `ollama create ... failed` message for any other failure. The note says the Ollama
   does not accept adapters, that nothing was wrong with the training, and what to do.
4. `register_with_ollama` finds the engine with `find_ollama_bin()` (bundled, first-run download, `~/bin`, `PATH`) and
   turns a missing engine into a `TrainerError`.

## Verified on real engines

- The probe on a real 0.40.16 engine: False; on a real 0.30.10 engine: True; on a running 0.40.7: False.
- `register_with_ollama` against a real 0.40.16 engine with a synthetic adapter folder raised the plain note.
- 211 existing and new training tests pass.

## Not done (the issue's next steps, which need evidence first)

- A route that does not use `ADAPTER` (merge the adapter into the weights before registering, or serve it behind the
  OpenAI-compatible endpoint). Choosing one needs a trained adapter on a small model, which needs training tools
  (mlx-lm, PEFT) installed and a base model downloaded, each needing the maintainer's go.
- A baseline on 0.30.10 with a correctly formed adapter (a synthetic one was refused there with an unrelated message).

## Acceptance

- `tests/test_adapter_registration_273.py` passes: the version gate, the probe's target server, the named refusal, the
  unchanged generic failure, the engine lookup, the early stop with no compute, the unchanged accepting and unknown
  cases, and the Apple Silicon path never asking.
- The existing training tests pass unchanged.
