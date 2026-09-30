# Tasks

- [x] Verify (primary sources) whether any of Berget/Groq/Infercom's terms, or any current
  `model_catalog.json` license (Apache-2.0, MIT, Gemma, Llama Community, NVIDIA Open Model,
  OpenMDW-1.1), restrict using model output to train another model - none do.
- [x] `app.py`: add `training_restricted: bool` to each `_INFERENCE_PROVIDERS` entry (`False` today,
  with the verified research cited in a comment).
- [x] `app.py`: new `_training_eligible(cfg) -> bool` - `False` only for a provider marked restricted;
  always `True` for self-provisioned cloud, on-prem/local, or an unrecognized/blank provider.
- [x] Wire `_training_eligible(cfg)` into `record_example`'s one real call site (Chat's response
  handler) instead of the previous implicit `True` default.
- [x] Tests (`tests/test_training_eligible_provider_gate.py`): local/on-prem, self-provisioned cloud
  (RunPod/Lambda), blank provider, and every live inference provider are all eligible today; a
  synthetic `training_restricted=True` provider (via monkeypatch) is correctly excluded; provider
  lookup is case/whitespace-insensitive.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2426 passed, 7
  skipped, no regressions).
