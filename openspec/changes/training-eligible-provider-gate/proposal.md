# Training-data capture: actually compute `training_eligible` per provider

Full spec: `docs/specs/training-eligible-provider-gate.md`.

## Why

`record_example()`'s `training_eligible` field (added in #661) exists to keep a captured chat turn out
of a later fine-tuning export when the model that produced it was reached through a provider whose own
terms forbid using its output to train another model - but nothing ever computed it. The one real call
site (Chat's response handler) never passed it, so every captured example has always defaulted to
`True` regardless of provider.

Checked against primary sources before wiring anything: none of Anthill's three live hosted inference
providers (Berget, Groq, Infercom) restrict using their API output for training - their "no training"
contract clauses run the other direction (the provider may not train on the customer's data). Every
model in `model_catalog.json` carries an open-weight license with no output-training restriction either
(Llama 3.3's only requires naming a distributed derivative "Llama*"; Gemma disclaims any rights in
outputs). So `training_eligible=True` for every account today is already correct - the gap is that
nothing would automatically protect against training on a genuinely restricted provider's output the
day one is added, unless someone remembers to wire it reactively at that point.

## What changes

- `_INFERENCE_PROVIDERS` (`anthill/web/app.py`) gains a `training_restricted: bool` field per provider
  (`False` for Berget/Groq/Infercom, with the verified research cited in a comment).
- New `_training_eligible(cfg) -> bool`: looks up `cfg.org_provider` in `_INFERENCE_PROVIDERS`, returns
  `False` only if that provider is marked restricted. Any provider not in the dict (self-provisioned
  cloud, on-prem/local, blank) is always eligible - those tiers only ever serve catalog models, none of
  which carry an output-training restriction.
- Wired into `record_example`'s one real call site (Chat's response handler):
  `training_eligible=_training_eligible(cfg)`.

## Guardrails (do NOT touch)

- No change to what gets captured, when, or the quality-promotion pipeline (thumbs-up / wiki approval)
  - only how `training_eligible` is computed.
- No per-model (catalog-level) restriction flag added - no current catalog entry's license needs one.
- No re-flagging of previously-captured `TrainingExample` rows - they were captured under providers
  verified to never have been restricted, so nothing needs correcting retroactively.
