# Semantic cache and wiki retrieval: move embeddings to Ollama, drop the torch/sentence-transformers dep

## Problem

QA/testing cross-check on the packaged desktop app (v0.11.18) found the semantic cache never functions
and wiki-page retrieval is degraded to keyword-only, permanently, in every real install:

- `Anthill.spec`/`Anthill-sidecar.spec` both `excludes=["torch", "sentence_transformers",
  "transformers"]` to keep the installer small - the comment on this said "omitted; app degrades to
  keyword-only," treating this as an accepted, permanent tradeoff.
- `anthill/cache/embedder.py`'s `available()` checked `importlib.util.find_spec("sentence_transformers")
  is not None` - permanently `False` in the frozen app, cached after the first check. `safe_embed()`
  then always returns `None`, so both the semantic cache (`anthill/cache/cache.py`) and wiki-page
  ranking (`wiki/ask.py`'s `_rank_by_embedding`, which both consume the same `embedder` module) are
  unreachable in every packaged install - not a gap in edge cases, the entire feature.
- No lazy first-run download existed to fill the gap despite a stray comment implying one ("first-run
  lazy download") - a frozen PyInstaller app cannot pip-install into itself, so this was never buildable
  as described.
- Symptom: the Metrics page's "cache hit rate" / "estimated savings from caching" read ~0 for every real
  user, and "answer from your wiki" (a core value prop) silently runs as keyword match, not semantic.

## Design

Ollama is *already* bundled with the packaged app (`Anthill.spec:57-59`, a self-contained
`ollama-runtime` folder) and already serves local chat. `embedder.py` now serves embeddings the same
way, through Ollama's `/api/embed`, instead of loading a local sentence-transformers model - no new
heavy dependency, no installer growth.

- **Same model, different runtime.** `MODEL_NAME` changes from `"BAAI/bge-m3"` (a sentence-transformers
  repo id) to `"bge-m3"` (Ollama's own model-library name) - the *same* BGE-M3 architecture, confirmed
  to output the same 1024 dimensions via Ollama's GGUF/llama.cpp serving instead of torch. `nomic-
  embed-text`, a more obvious-looking lightweight choice, is NOT a substitute here - it's 768-dim and
  would silently break the existing hardcoded `pa.list_(pa.float32(), 1024)` LanceDB schema
  (`anthill/cache/store.py`). `DIM = 1024` is unchanged.
- **Same public contract.** `available()`, `embed()`, `safe_embed()`, `cosine()` keep their exact
  signatures and never-crash-on-missing-model behavior, so `cache.py` and `wiki/ask.py` needed zero
  changes. `available()` now does a live reachability + tags check against Ollama instead of a Python
  import check - cached once `True` (an install that has the model keeps it), retried on `False` (the
  model might still be mid-download, or Ollama might not be up yet at this exact moment).
- **Explicit normalization.** Ollama's `/api/embed` returns raw (non-unit) vectors, unlike
  sentence-transformers' `normalize_embeddings=True` this replaces. `embed()` L2-normalizes explicitly
  so `cosine()`'s "already normalized, dot product == cosine similarity" assumption keeps holding.
- **First-run pull.** A new `_maybe_pull_embedding_model()` in `anthill/web/app.py`'s startup sequence
  (alongside the other best-effort, `try/except`-wrapped boot tasks) checks `embedder.available()` and,
  if the model isn't there, shells out to `ollama pull bge-m3` in a background thread - the same
  `find_ollama_bin`/`ensure_serving` pattern `_start_model_pull` already uses for chat models. Not
  org-scoped (embeddings are a shared, install-level resource, unlike a chat model choice), so it runs
  once per server process rather than being wired into every per-org model-pull call site.
- **Stale-cache migration.** `anthill/cache/store.py`'s LanceDB table is renamed `"cache"` ->
  `"cache_v2"`. Vectors from the old sentence-transformers stack and the new Ollama stack are the same
  architecture/dimension but not bit-identical (different quantization/runtime), so mixing them in one
  table would let stale rows return subtly-off similarity scores. Renaming orphans any pre-existing
  table (harmless, sits unused on disk) rather than adding migration code - a fresh, correctly-versioned
  cache builds up from empty, which is an acceptable cost for what is fundamentally a performance
  optimization, not a source of truth.
- **`torch`/`sentence-transformers`/`transformers` excludes kept, reframed.** Nothing in `anthill/`
  imports these anymore, so PyInstaller wouldn't bundle them regardless - the excludes stay as a
  defensive measure against some other dependency's optional import path accidentally reintroducing
  ~2GB, not because anything currently needs them omitted. `sentence-transformers` is removed from
  `pyproject.toml`'s base `dependencies` entirely (dead weight now, previously required even for a
  plain `pip install anthill`); the `ci` extras' now-stale "avoid pulling PyTorch" comment is corrected
  to describe what it actually does (a minimal, hand-picked test-only dependency set).

## A real bug this surfaced (fixed alongside, not the original scope)

Making `embedder.available()`/`embed()` genuinely, reliably unreachable in the test environment (via
`tests/conftest.py`'s existing hermetic-Ollama block, extended to cover this new HTTP-based path)
exercised `wiki/ask.py`'s keyword-only fallback (`_keyword_fallback`) for the first time in a way that
mattered locally - previously, any dev machine with sentence-transformers actually installed silently
took the real semantic path instead, every time, so this fallback's own bugs went unnoticed outside CI's
`.[ci]` job (which has never installed sentence-transformers). Found: `_keyword_fallback`'s search-term
filter dropped every 2-letter word outright (`len(t) > 2`), so a query like "what db?" against a page
titled exactly "DB" matched nothing. Fixed to `>= 2` - a last-resort fallback (only reached when both
Meilisearch and real embeddings are unavailable) trading a little short-stopword noise for not silently
losing real technical abbreviations ("db", "ai", "ui", "os", "ml", "vm", "id") outright. Also hardened
`test_ask_stream.py`'s grounding test, which the same silent local/CI divergence let go undetected, to
force the fallback path explicitly rather than accidentally depending on whatever embedding backend
happened to be importable.

## Also caught building this: a real test-hermeticity gap

`anthill/web/app.py`'s `_maybe_pull_embedding_model()`, wired into the FastAPI startup event, escaped
`tests/conftest.py`'s existing "no live Ollama in tests" protection - not because that protection is
wrong, but because this function's OWN fallback when it sees "not available" is to shell out to a real
`ollama pull` subprocess, which isn't an httpx call at all, so the fixture's transport-level block
can't reach it. Caught live: on this dev machine (which has a real, working Ollama install), the first
test in the suite that spins up a FastAPI `TestClient` fired the startup event and triggered a genuine
~1.2GB download mid test-run. Fixed by stubbing the function itself in `conftest.py`'s autouse fixture
(matching how `OllamaBackend.chat`/`chat_with_tools` are already stubbed directly, not just blocked at
the transport layer) - `tests/test_embedding_model_bootstrap.py` restores the real implementation to
test its own logic in isolation.

## Out of scope

- Auto-clearing the old `"cache"` LanceDB table from disk - it's simply orphaned/unused, not deleted;
  actively cleaning it up is a separate, low-priority housekeeping task.
- Any change to the Metrics page's "cache hit rate"/"estimated savings" copy - a QA-owned, independent
  fix (grey/caveat the copy while embeddings are unavailable), tracked separately.
- Rewriting `_keyword_fallback`'s scoring beyond the minimal length-filter fix - it remains a coarse,
  last-resort mechanism by design, not a precision search engine.
