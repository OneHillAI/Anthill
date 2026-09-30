# Semantic cache and wiki retrieval: move embeddings to Ollama, drop torch/sentence-transformers

Full spec: `docs/specs/ollama-served-embeddings.md`.

## Why

QA/testing found the shipped desktop app runs semantic-cache-and-wiki-retrieval keyword-only,
permanently, in every real install: `Anthill.spec`/`Anthill-sidecar.spec` exclude
`torch`/`sentence_transformers`/`transformers` to keep the installer small, `embedder.available()`
checked whether `sentence_transformers` was importable (permanently False in the frozen app), and no
runtime lazy-download existed to fill the gap (a frozen PyInstaller app can't pip-install into itself).
Ollama is already bundled and already serves local chat - `embedder.py` now serves embeddings the same
way, closing the gap with no installer growth.

## What changes

- `anthill/cache/embedder.py`: `available()`/`embed()` now talk to Ollama's `/api/tags`/`/api/embed`
  instead of loading a local sentence-transformers model. Same public contract (`available()`,
  `embed()`, `safe_embed()`, `cosine()`, `DIM = 1024`), so `cache.py`/`wiki/ask.py` need zero changes.
  `MODEL_NAME` becomes `"bge-m3"` (Ollama's model-library name for the same architecture, confirmed
  1024-dim - NOT `nomic-embed-text`, which is 768-dim and would silently break the schema). Explicit
  L2-normalization, since Ollama returns raw vectors unlike sentence-transformers' own normalization.
- `anthill/web/app.py`: new `_maybe_pull_embedding_model()` in the startup sequence - pulls `bge-m3` in
  the background on first run if it isn't already there, reusing the `find_ollama_bin`/`ensure_serving`
  pattern `_start_model_pull` already uses. Not org-scoped (a shared, install-level resource).
- `anthill/cache/store.py`: LanceDB table renamed `"cache"` -> `"cache_v2"` - a pre-existing table's
  vectors (from the old sentence-transformers stack) aren't safely comparable to new Ollama-served
  ones, so this orphans stale rows rather than mixing them; no migration code needed.
- `pyproject.toml`: `sentence-transformers` removed from base `dependencies` entirely (unused now,
  previously required for every `pip install anthill`); `ci` extras' stale "avoid pulling PyTorch"
  comment corrected. `Anthill.spec`/`Anthill-sidecar.spec`'s excludes are kept (defensive, in case some
  other dependency's optional import path reintroduces the weight) but their comments reframed - they
  no longer explain the keyword-only degradation, since that's fixed.
- `anthill/wiki/ask.py`: `_keyword_fallback`'s term filter changed from `len(t) > 2` to `>= 2` - a real,
  pre-existing bug this change's more reliable test hermeticity surfaced (a 2-letter query term like
  "db" was dropped outright, so "what db?" against a page titled "DB" matched nothing).
- `tests/conftest.py`: the autouse hermetic-Ollama fixture now also stubs `_maybe_pull_embedding_model`
  directly - its own fallback (shelling out to `ollama pull`) isn't an httpx call, so the fixture's
  existing transport-level block couldn't reach it; caught live via a genuine ~1.2GB download mid
  test-run on a dev machine with real Ollama installed.

## Guardrails (do NOT touch)

- No change to `cache.py`'s or `wiki/ask.py`'s own logic - both consume `embedder.py` through the exact
  same contract as before.
- No change to the Metrics page's "cache hit rate"/"estimated savings" copy - tracked separately (QA).
- No auto-deletion of the old `"cache"` LanceDB table - orphaned, not cleaned up, in this change.
- No rewrite of `_keyword_fallback`'s scoring approach beyond the minimal length-filter fix - stays a
  coarse last-resort mechanism, not a precision search engine.
