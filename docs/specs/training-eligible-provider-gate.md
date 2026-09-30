# Training-data capture: actually compute `training_eligible` per provider

## Problem

`record_example()` (`anthill/training/collect.py`) has captured every successful chat turn as a
`TrainingExample` since the inference-provider work (#661), with a `training_eligible` field whose
docstring says it should be `False` "when a contributing model was reached through a third-party
closed-model API whose own terms restrict using its outputs to train another model." That field was
added and tested at the storage layer (`tests/test_training_capture_fields.py`) but never actually
computed anywhere - the one real call site (Chat's response handler, `app.py`) never passed it, so
every captured example has always defaulted to `training_eligible=True` regardless of which backend
answered.

Before wiring anything, the actual current risk was checked against primary sources rather than
assumed:

- Groq, Berget, Infercom (Anthill's three live hosted inference providers): none restrict using their
  API output to train another model. Their "no training" contract language runs the other direction -
  the PROVIDER may not train on the CUSTOMER's data, not "the customer may not use my output."
- Every model in `model_catalog.json` (self-provisioned "your cloud" and local tiers) carries an
  open-weight license - Apache-2.0, MIT, Gemma, Llama Community, NVIDIA Open Model, OpenMDW-1.1 - and
  none of those restrict output use for training either. Llama 3.3's Community License (checked
  directly against Meta's published license text) only requires that a distributed derivative model be
  *named* with a "Llama" prefix; it does not prohibit using outputs to train one. Gemma's terms
  explicitly disclaim any Google rights in outputs.

So there is no live gap today: `training_eligible=True` for every account is already the correct value
for every provider Anthill actually integrates. The gap is structural, not behavioral - nothing
computes this from real data, so the day a genuinely training-restricted provider is added, nothing
would automatically protect against training on its output unless someone remembers to wire it then.

## Design

`_INFERENCE_PROVIDERS` (`anthill/web/app.py`) gains a `training_restricted: bool` field per provider -
`False` for Berget/Groq/Infercom today, with a comment citing the verified research above. A new
`_training_eligible(cfg) -> bool` looks up `cfg.org_provider` in that dict and returns `False` only if
the provider is marked restricted; any provider not in the dict (self-provisioned cloud, on-prem/local,
blank) is always eligible, since those tiers only ever serve catalog models with no such restriction.

Wired into the one real call site - Chat's response handler - as
`record_example(..., training_eligible=_training_eligible(cfg))`. No other call site exists.

This is intentionally the narrowest correct primitive: a lookup keyed on the provider actually used for
that turn, not a broader per-model license-parsing system (unnecessary today, since no catalog license
restricts output use) and not something invented per-model in `model_catalog.json` (there is nothing to
flag there right now). If a future model's license *does* carry an output-training restriction, that
would need its own field on the catalog entry and a corresponding check here - not built now, since no
current entry needs it.

## Out of scope

- Any change to what gets captured, when, or the existing quality-promotion pipeline (thumbs-up / wiki
  approval) - this only changes how `training_eligible` is computed, not the rest of `record_example`'s
  behavior.
- A per-model (catalog-level) training-restriction flag - no current catalog entry's license needs one;
  add it if and when one does.
- Auditing or re-flagging `TrainingExample` rows captured before this change - they were captured under
  providers that, per the same research, were never actually restricted, so no correction is needed.
