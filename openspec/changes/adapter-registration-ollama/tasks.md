# Tasks

- [x] `trainer.local_ollama_accepts_adapters()` with the 0.34.1 boundary, the probe target and an unknown answer.
- [x] The executor stops a non-MLX run before the backend runs; the Apple Silicon path never asks.
- [x] `register_with_ollama` names the refusal, finds the engine with `find_ollama_bin`, reports a missing engine.
- [x] Tests; check the probe and the registration against real 0.30.10 and 0.40.16 engines.
- [ ] A baseline on 0.30.10 with a correctly formed adapter (needs a trained adapter).
- [ ] Choose and build a registration route that does not use `ADAPTER` (needs the maintainer's go to install tools).
