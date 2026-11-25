# Tasks

- [x] Spec in `docs/specs/adapter-merged-gguf.md`; the routes tested on real engines (2026-11-24 and 2026-11-25).
- [x] `merge_adapter_to_gguf`, run by `train_adapter` when the image configures the converter.
- [x] `register_with_ollama` registers `merged.gguf` with the base template on any engine; the `ADAPTER` route is the fallback.
- [x] The early stop removed; `docker/Dockerfile.trainer` pins the converter by tag and checksum.
- [x] Tests that fail without the change.
- [ ] Owner: rebuild and validate the trainer image on a real GPU host; named review of the protected path.
