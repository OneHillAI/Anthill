# Spec: honest Metrics cache copy when embeddings are unavailable

Status: implemented. Lane: `pillar:platform`.

## Problem

The packaged desktop app ships lean: `Anthill.spec` and `Anthill-sidecar.spec` exclude
`torch`/`sentence_transformers`/`transformers` to keep the dmg small, so `anthill/cache/embedder.py::
available()` is False in the frozen app and the semantic cache is skipped (retrieval and cache both fall
back to keyword matching). The Metrics page nonetheless renders "Cache hit rate" and "Estimated savings
from caching" unconditionally, so a real user always sees `0%` / `0 of N from cache` / `$0` - which reads
as broken rather than as "this feature needs a component that isn't installed".

(The underlying capability - serving embeddings via the bundled Ollama so the semantic cache actually
works - is a separate change, owned elsewhere. This spec is only the honesty of the display.)

## Change

`metrics_page` passes `semantic_cache_available = embedder.available()` to the template. In
`metrics.html`:

- The "Cache hit rate" card shows the real percentage when embeddings are available, and otherwise a
  short caveat ("Semantic cache is off in this build - it needs an embedding model, so repeat questions
  are not reused yet") in place of a permanent 0%.
- The "Estimated savings from caching" card shows the real estimate when available, and otherwise
  explains there are no cache savings yet because the embedding model is not active (the in-perimeter
  percentage, which is independent of the cache, still shows).

No behaviour change when embeddings ARE present (a full/source install): the real numbers render exactly
as before. The gate is `embedder.available()`, the same signal the cache and wiki ranking already use to
decide whether to degrade to keyword-only, so the page can never disagree with the actual cache state.

## Verification

`tests/test_metrics.py` / `test_metrics_auth.py` stay green (available path). The unavailable path is a
simple template branch; both branches were confirmed to render and the template parses. British spelling,
no em/en dashes.
