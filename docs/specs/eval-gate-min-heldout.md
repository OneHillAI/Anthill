# Spec: The promotion gate won't decide on a trivially small or order-biased eval slice

Status: implemented. Lane: `pillar:model`. Issue: #365 (decision-rule hardening; the data-leakage core
was already fixed by the `_split_gold` held-out slice).

## Problem

The training promotion gate scores a fine-tuned adapter against the live model on a held-out slice of
gold and promotes the candidate only if it wins. Two decision-rule weaknesses remained after the
leakage fix:

1. **Tiny held-out set.** `_split_gold` holds out ~20% of distinct instructions above
   `MIN_GOLD_FOR_SPLIT` (6). At the low end that is a single held-out instruction, so a *validated*
   promotion (replacing a live model) could hinge on one row. One row is noise, not a regression
   signal, and a regression could ship on a coin-flip.
2. **Order-biased sampling.** `evaluate_models` scored `examples[:sample]` - the first N in whatever
   order the split produced - so the scored subset was biased by ordering rather than representative.

## Policy

- **Minimum held-out size for a validated promotion.** Replacing a live model (`training_model_ver > 0`)
  requires at least `MIN_EVAL_EXAMPLES` (5) distinct held-out instructions. Below that the run is
  rejected and the incumbent is kept, recorded without spending training compute (possibly a rented
  GPU). First models (no incumbent) stay exempt: something validated-or-not beats nothing, so
  `allow_unvalidated` still lets a first model through.
- **Order-independent scoring.** When more held-out examples are available than `sample`,
  `evaluate_models` draws a reproducible pseudo-random subset (fixed seed) instead of the first N. With
  `<= sample` examples, all are scored (unchanged).

This is deliberately a floor, not a claim of statistical significance. Real evaluation rigor (identical
retrieval/prompt across arms, groundedness and schema metrics over cosine, a base+RAG ROI anchor, a
capability/safety canary, canary rollout with rollback) is a larger evaluation-design program tracked
separately, out of scope here.

## Acceptance criteria

- An incumbent org (`training_model_ver > 0`) with too few distinct held-out instructions
  (`< MIN_EVAL_EXAMPLES`) is rejected without training, keeping the live model version.
- An incumbent org with `>= MIN_EVAL_EXAMPLES` held-out instructions reaches the gate with
  `allow_unvalidated=False`.
- A first model (no incumbent) still promotes below the split threshold (`allow_unvalidated=True`).
- `evaluate_models` scores at most `sample`, the subset is deterministic across runs and is not simply
  the first N, and a set of `<= sample` examples is scored in full.
- Covered by `tests/test_training_execution.py` and `tests/test_eval_sampling.py`.
