# Tasks

- [x] `trainer.local_ollama_accepts_adapters()` with the 0.34.1 boundary, the probe target and an unknown answer.
- [x] The executor stops a non-MLX run before the backend runs; the Apple Silicon path never asks.
- [x] `register_with_ollama` names the refusal, finds the engine with `find_ollama_bin`, reports a missing engine.
- [x] Tests; check the probe and the registration against real 0.30.10 and 0.40.16 engines.
- [x] A baseline on 0.30.10 with a correctly formed adapter: see `docs/specs/adapter-merged-gguf.md` (a Llama base works only with the weights named `model.safetensors`; the app's own folder fails).
- [x] The registration route that does not use `ADAPTER`: the merged GGUF, `openspec/changes/adapter-merged-gguf/`.
