# Tasks

- [x] Verify the finding: `Anthill.spec:98`/`Anthill-sidecar.spec:82` exclude torch/sentence_
  transformers/transformers; `embedder.available()` permanently False in the frozen app; no lazy-
  download mechanism anywhere; `wiki/ask.py` shares the same embedder, so cache + wiki retrieval are
  one root cause, not two.
- [x] Confirm Ollama's `bge-m3` model outputs 1024 dimensions (matches the existing hardcoded LanceDB
  schema exactly); confirm `nomic-embed-text` (768-dim) would NOT be a safe substitute.
- [x] Rewrite `anthill/cache/embedder.py`: `available()`/`embed()`/`safe_embed()`/`cosine()` keep their
  exact contract, now backed by Ollama's `/api/tags`/`/api/embed`; explicit L2-normalization (Ollama
  returns raw vectors).
- [x] `anthill/web/app.py`: `_maybe_pull_embedding_model()` wired into the startup sequence, first-run
  background pull via `find_ollama_bin`/`ensure_serving`, not org-scoped.
- [x] `anthill/cache/store.py`: rename the LanceDB table `"cache"` -> `"cache_v2"` (stale-vector
  migration via orphaning, no migration code).
- [x] `pyproject.toml`: remove `sentence-transformers` from base `dependencies`; correct the `ci`
  extras' stale comment. `Anthill.spec`/`Anthill-sidecar.spec`: reframe the excludes' comments (kept as
  a defensive measure, no longer the reason for keyword-only degradation).
- [x] Tests: `tests/test_embedder_ollama.py` (new - available()/embed()/safe_embed() against the real
  Ollama-backed implementation, httpx-mocked); `tests/test_embedder_fallback.py` (refreshed prose,
  same regression coverage); `tests/test_embedding_model_bootstrap.py` (new - the startup pull hook,
  all four branches: already-available, pulls, no binary, Ollama never comes up, never raises).
- [x] Found and fixed, surfaced by this change's more reliable test hermeticity: `wiki/ask.py`'s
  `_keyword_fallback` dropped 2-letter query terms outright (`len(t) > 2` -> `>= 2`); new
  `tests/test_keyword_fallback.py`; hardened `tests/test_ask_stream.py`'s grounding test to force the
  fallback path explicitly instead of accidentally depending on local sentence-transformers
  availability.
- [x] Found and fixed, caught live during testing: `_maybe_pull_embedding_model()` escaped
  `tests/conftest.py`'s hermetic-Ollama protection (its own `ollama pull` subprocess fallback isn't an
  httpx call) - triggered a real ~1.2GB download mid test-run on this dev machine; stubbed directly in
  the autouse fixture, matching `chat()`/`chat_with_tools()`'s existing treatment.
- [x] Full local validation: `ruff check`, `ruff format --check`, `mypy`, full `pytest` (2444 passed,
  10 skipped, no regressions).
