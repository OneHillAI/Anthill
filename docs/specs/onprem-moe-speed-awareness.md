# Spec: On-prem MoE speed-awareness + region-aware council suggestions

Status: proposed
Lane: `pillar:model`
Relates to: `anthill/hosting/sizing.py` (`recommend()`, `recommend_by_family()`, `Model`, `ModelFit`), `docs/specs/local-vs-frontier-capability-roadmap.md` (PR #661, Tier 0/1 findings this spec acts on), `docs/specs/product-council-architecture.md` (council member composition, R3)

## 1. Introduction

Anthill's on-prem model picker (`anthill/hosting/sizing.py`) sizes purely on `params_b` (total parameters) - correct for MEMORY (every expert of a Mixture-of-Experts model must be resident, whether or not it computes this token), but not for SPEED (a MoE model only computes its active experts per token, `active_b`). A model can be memory-feasible and score well on `intelligence`, yet still be too slow to be a usable product - true of a large dense model on modest hardware just as much as an under-provisioned MoE model, since neither `params_b` nor `intelligence` captures real-world tokens/sec.

This spec adds a hard usable-speed floor to the picker (excluding, not deprioritizing, anything estimated too slow) and a region-aware council-composition suggestion helper that is honest about real model-family scarcity by region.

## 2. Requirements

### R1 - A tokens/sec estimate, MoE-aware
- THE SYSTEM SHALL estimate tokens/sec for a model on the detected Apple Silicon chip from memory bandwidth and `active_b` (falling back to `params_b` when `active_b` is unset or equal to it, i.e. a dense model).
- THE SYSTEM SHALL apply a MoE-specific overhead discount ONLY when a model is genuinely MoE (`active_b < params_b`); a dense model SHALL use the raw bandwidth-bound estimate, calibrated separately against real published dense-model benchmarks.
- THE SYSTEM SHALL return "cannot estimate" (never guess) for non-Apple-Silicon hardware or an unrecognized chip, and callers SHALL treat that as "do not apply a speed floor."

### R2 - A hard usable-speed floor, applied to every model
- THE SYSTEM SHALL exclude a model from recommendation when its estimated speed is below a defined usable-speed floor (~10 tokens/sec), regardless of its `intelligence` score.
- THE SYSTEM SHALL apply this floor to dense and MoE models alike - a large dense model on modest hardware can be just as unusably slow as an under-provisioned MoE model.
- THE SYSTEM SHALL treat this as a hard exclusion (a real-option filter), not a soft preference or a trade-off against intelligence.

### R3 - The existing capacity-based tiebreak is unchanged
- THE SYSTEM SHALL NOT modify the existing "smartest that fits" intelligence/capacity tiebreak (`(intelligence, params_b)`, larger `params_b` wins when `intelligence` ties). That comparison answers a capacity question; the speed floor answers a usability question. They SHALL remain two separate mechanisms.

### R4 - Honest, memory-vs-speed-distinguishing UI signal
- THE SYSTEM SHALL distinguish "fits in memory" from "estimated too slow" as separate signals (`ModelFit.fits` vs `ModelFit.too_slow`), so a too-slow-but-memory-feasible model is never described to the user as needing more memory.

### R5 - Region-aware council-composition suggestions, honest about scarcity
- THE SYSTEM SHALL provide a helper that, given a region (US/EU/China), reports the distinct model families available in that region in the current catalog.
- THE SYSTEM SHALL explicitly state when a region lacks enough distinct families (fewer than 3) for a genuinely diverse same-region council, rather than silently substituting or implying diversity that is not there.

## 3. Acceptance criteria

- A dense model's speed estimate is unaffected by the MoE overhead discount; a genuine MoE model's estimate is.
- The floor correctly excludes a large dense model on low-bandwidth Apple Silicon (e.g. M1/M2/base M4) and correctly permits it on higher-bandwidth chips (M3 Max and above), matching real published Llama 3.3 70B benchmarks.
- The floor correctly permits a MoE model where an equally-sized dense model would be excluded, on the same hardware.
- Unrecognized/non-Apple hardware never triggers exclusion - behavior identical to before this change.
- The existing intelligence/capacity tiebreak's code is provably unmodified.
- Region suggestions match the real, current catalog's family counts (verified, not assumed): China 5 families, US 4, EU 1.
