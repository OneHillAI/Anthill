# On-prem MoE speed-awareness + region-aware model/council suggestions

## Why (revised - supersedes the first draft of this proposal)

The first draft of this change proposed wiring `active_b` into the existing "smartest model that fits"
intelligence-tiebreak. **That direction was wrong and is explicitly abandoned here.** The existing
tiebreak (`(m.intelligence, m.params_b) > (best.intelligence, best.params_b)`, prefer larger `params_b`
when `intelligence` ties) is about CAPACITY - a bigger model at equal intelligence might have nuance the
coarse score missed. `active_b` measures something different - SPEED, compute cost per token. Using one
in place of the other conflates two independent axes and would silently change what the tiebreak was
built to express. Do not touch that comparison.

**What replaces it, decided directly with the human maintainer, in two parts:**

1. **A hard speed floor, not a soft preference.** A model estimated below a usability threshold is
   EXCLUDED from consideration entirely - not deprioritized, not shown with a caveat. Rationale stated
   directly: "slowness means usability, and usability is a real friction. If it's too slow, it can't
   work." This deliberately avoids the harder problem of trading off intelligence against speed (no
   formula, no user-facing slider needed) - it only answers "is this a real option at all," then ranks by
   intelligence exactly as today among whatever survives the floor.
2. **Region-aware suggestions for council composition that are honest about real scarcity.** Verified
   against Anthill's actual 24-model catalog (`anthill/hosting/model_catalog.json`), not assumed: China
   has 5 distinct model families (DeepSeek, MiniMax, GLM, Kimi, Qwen), the US has 4 (OpenAI/gpt-oss,
   Meta/Llama, Google/Gemma, Microsoft/Phi), and the EU has exactly 1 (Mistral AI). A same-region,
   family-diverse council of 3 is achievable for US or China; it is NOT achievable for EU today - the
   product must say so plainly rather than imply an EU-only diverse council exists.

## Part 1: a real tokens/sec estimate and a hard exclusion floor

### The physics (verified against a real published data point, not asserted from theory alone)

For memory-bandwidth-bound local inference (the normal case for single-user serving), a first-order
estimate is `tokens_per_sec ≈ memory_bandwidth_GBps / (active_b × bytes_per_param)`. At 4-bit
quantization, `bytes_per_param ≈ 0.5`. Cross-checked against a real, independently-published result: an
M3 Ultra (819 GB/s memory bandwidth) running DeepSeek-V3 (active_b=37, 4-bit) measures ~17-21 tok/s in
published third-party benchmarks (VentureBeat, MacRumors, Hardware Corner, MacStories - see the earlier
research in this thread). The naive formula gives `819 / (37 * 0.5) ≈ 44 tok/s` - roughly 2x the measured
real-world number, consistent with real overhead (attention compute, KV cache reads, framework overhead)
not captured by the simple bandwidth model. **Apply a calibration factor (~0.45-0.5x the naive estimate)
derived from this real data point**, and state explicitly in the PR that this is a first-order estimate,
not a guarantee - it exists to draw a defensible line, not to promise an exact number.

### The threshold (grounded in published UX/latency research, not an arbitrary number)

- **~6 tok/s** approximates average human reading speed (250 words/min) - below this the model is
  literally slower than a person reads text, unconditional friction.
- **~10 tok/s** (100ms/token) is where streaming starts to feel smooth rather than halting - a defensible
  hard floor.
- **~40 tok/s** is where the experience qualitatively transforms (the 10-to-40 jump is described as
  dramatic in the research); gains above 40 diminish since reading speed becomes the bottleneck, not the
  model.

**Use ~10 tok/s as the hard exclusion floor** (below it, a model is not offered as an option regardless
of intelligence), and treat ~40 tok/s as the marker worth surfacing to the user as "smooth" if the product
wants a positive indicator, not just a cutoff.

### What's needed that doesn't exist yet: a chip-to-bandwidth table

`anthill/hosting/sizing.py`'s `local_hardware()` only returns memory CAPACITY (`_macos_mem_gb()`), not
memory BANDWIDTH - a different, chip-specific number the estimate above needs. Gathered so far (verify
against Apple's own published tech specs before shipping - these are cross-referenced from public
benchmarks, not Apple's own spec sheets, and precision matters here since this feeds a hard gate):

| Chip | Memory bandwidth (GB/s), approx |
|---|---|
| M1 | ~70 |
| M2 | ~102 |
| M3 Pro | ~154 |
| M3 Max (16-core) | ~400 |
| M3 Ultra | ~819 |
| M4 Max | ~410-546 (varies by config) |
| M5 | ~154 |
| M5 Pro | ~307 |

Gaps exist (M1/M2 Pro/Max/Ultra variants, M4 base/Pro, any M5 Max/Ultra) - **do not ship a chip-bandwidth
table without verifying every entry against `support.apple.com` tech specs for the exact model
identifiers Anthill will actually see** (`platform.machine()`/`sysctl` on macOS report a chip ID, not a
marketing name - confirm what's actually queryable before assuming any specific string match works).
Missing/unrecognized chips must degrade safely - fall back to the current, conservative params_b-only
behavior (no speed floor applied) rather than guess a bandwidth number.

## Part 2: region-aware model/council suggestions, honest about scarcity

### Real family-per-region counts (verified against the actual 24-model catalog, not assumed)

```
China: DeepSeek, MiniMax, GLM, Kimi, Qwen        -> 5 distinct families
US:    gpt-oss (OpenAI), Llama (Meta),
       Gemma (Google), Phi (Microsoft)            -> 4 distinct families
EU:    Mistral (Mistral AI)                       -> 1 family
```
US-origin intelligence scores at comparable sizes cluster reasonably close (gpt-oss 20B=48, Llama 3.3
70B=46, Gemma 3 27B=44, Phi-4 14B=40) - a genuine same-region, family-diverse, comparable-strength triplet
is achievable for the US today. It is NOT achievable for the EU with only one family in the catalog.

### What to build

A suggestion helper (placement - new function in `sizing.py` or a thin wrapper in `app.py`'s settings
route, whichever avoids duplicating catalog-filtering logic that already exists) that, given a region/
sovereignty preference:
- If enough distinct families exist in that region at a comparable strength tier, suggest a real,
  diverse, same-region triplet (reusing `families_by_intelligence`/the existing family-grouping helpers -
  do not reinvent family grouping, it already exists).
- If not (concretely: EU today), say so explicitly rather than silently substituting or silently
  presenting a non-diverse EU-only set as if it were the ideal - offer the single strong same-region
  option plus a clearly-labeled "mix in non-EU reviewers for real diversity" alternative, so the org makes
  an informed sovereignty-vs-diversity trade-off rather than getting an implicit, unexplained one.

This reuses the `intelligence`/`family`/`origin` fields already in the catalog (verified real and
populated, see the table above) - no new catalog metadata needed for this part.

## Acceptance Criteria

1. A new function estimates tokens/sec from `active_b` (falling back to `params_b` for a dense model,
   i.e. `active_b == 0` or `active_b == params_b`) and a bandwidth figure for the detected chip, applying
   the ~0.45-0.5x calibration derived from the DeepSeek-V3/M3-Ultra data point. Missing/unrecognized
   hardware degrades to "unknown - do not apply the floor" rather than guessing.
2. A hard exclusion floor (~10 tok/s, a named constant, not a magic literal) removes a model from
   consideration in the on-prem picker BEFORE intelligence-based ranking runs - it is a filter, not a
   tiebreak input. The existing `(m.intelligence, m.params_b)` tiebreak comparison is completely
   unmodified - confirm this with a test that fails if that comparison's inputs ever change.
2b. On hardware where the estimate can't be computed (unrecognized chip, non-Apple-Silicon, capacity
    unknown), NO model is excluded on speed grounds - behavior is identical to before this change.
3. Dense models (`active_b` unset or equal to `params_b`) are completely unaffected by the speed floor at
   any realistic size, since their speed already scales with size the same way memory does - state
   explicitly in the PR why this is expected to be a no-op for the dense-model catalog entries, backed by
   the actual numbers (e.g. confirm Llama 3.3 70B doesn't get excluded on a machine where it fits in
   memory, using this same formula).
4. A region-aware suggestion helper, reusing existing family/intelligence catalog fields, that (a)
   proposes a real diverse triplet where the data supports one (US, China), and (b) explicitly surfaces
   the EU scarcity rather than silently working around it - tested against the real current catalog
   counts (5 China / 4 US / 1 EU families), not invented test fixtures that don't reflect reality.
5. New tests for both parts, using the REAL catalog's actual MoE entries (gpt-oss 20B active_b=3.6,
   Qwen3.6 35B-A3B active_b=3, DeepSeek V3.1 active_b=37 params_b=671, etc.) and real family/origin
   values - not synthetic fixtures that could hide a wrong assumption about what's actually in the
   catalog.
6. `ruff check`, `ruff format --check`, `mypy`, full test suite pass. No em/en-dashes, no TODO/FIXME/XXX
   markers.

## Explicitly out of scope

- VPC/cloud serving, multi-GPU tensor-parallel (#636's territory) - untouched.
- Expanding the model catalog's list of entries (e.g. adding more EU-origin models to close the gap) -
  that's a data/content decision for whoever owns the catalog, not a logic change; this proposal makes the
  scarcity visible and honest, it does not manufacture EU diversity that doesn't exist.
- A user-facing "trade speed for intelligence" slider - explicitly rejected in favor of the hard-floor-
  then-rank-by-intelligence approach above.
- Verifying every Apple Silicon chip's exact bandwidth figure against Apple's own tech-specs pages - the
  table above is a starting point gathered from public benchmarks; whoever implements this must verify it
  before shipping (called out explicitly above), but doing that verification exhaustively is not this
  proposal's job to complete in advance.
