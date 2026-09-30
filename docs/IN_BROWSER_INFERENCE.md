# Building out in-browser inference (contributor guide)

A guide for picking up the **in-browser inference** feature and building it to completion. A model runs
**entirely in the browser tab** (WebGPU/WASM) - no server, no install, nothing leaves the device. This
is a **zero-install / personal-lite** surface (solo plane only); it never replaces the native desktop
or org-server paths.

**Status:** Phase 0 (a prototype) is merged and behind a flag. Phases 1-4 are open - that is the work.

---

## 1. See what already exists (Phase 0)

The prototype is a single flag-gated page that loads WebLLM + a small model and streams a reply in-tab.

Run it in a **WebGPU browser** (Chrome/Edge desktop, or Safari 18+):

```bash
pip install -e .          # or: bash start.sh
ANTHILL_LITE=1 anthill serve
# open http://localhost:8000/lite , click "Load model", chat
```

Note the two numbers it reports - **model-load time** and **first-token latency**. Those are the signal
for whether the surface is worth investing in; capture them on a laptop and a low-end machine.

**Files:**
- `anthill/web/app.py` - the `GET /lite` route (gated by the `ANTHILL_LITE` env var, unauthenticated).
- `anthill/web/templates/lite.html` - the standalone page (WebGPU detection, WebLLM load, streaming, metrics).
- `tests/test_lite.py` - serving/wiring test (WebGPU + a model download cannot run in CI; real inference is verified manually).

---

## 2. The open-source tools (each a different job; all permissive)

| Tool | Package | Licence | Role |
| --- | --- | --- | --- |
| **WebLLM** | `@mlc-ai/web-llm` | Apache-2.0 | Primary chat engine on **WebGPU**. OpenAI-compatible API + streaming. Already used in Phase 0. |
| **wllama** | `@wllama/wllama` | MIT | **No-WebGPU fallback** - runs GGUF on CPU via WASM. For browsers without WebGPU. |
| **transformers.js** | `@huggingface/transformers` | Apache-2.0 | **Embeddings** for client-side wiki grounding (RAG in the tab). |

Constraint: **open-source tooling only.** Chrome's proprietary Gemini Nano / Prompt API is intentionally
**excluded** (off-thesis). Prefer Apache/MIT-licensed open *weights* for any shipped model.

---

## 3. The build-out (Phases 1-4)

- **Phase 1 - engine abstraction + fallback.** Extract a JS module (e.g. `static/browser_llm.js`) exposing
  one `streamChat(messages) -> async iterator of tokens`, backed by **WebLLM** when `navigator.gpu`
  exists and **wllama** when it doesn't. Add a **device-sized model picker** (default a 1-3 B model;
  warn on download size) and cache models in OPFS / Cache Storage. Generalise `/lite` to use it.
- **Phase 2 - wiki grounding in-browser (the OKGF tie-in).** Load an **OKGF bundle** (`.tgz`) in the tab,
  read its pages in JS (mirror `anthill/wiki/okf.py: parse_bundle`), embed them with **transformers.js**,
  retrieve top-k for each question, and build the grounded prompt the server already uses
  (`anthill/wiki/ask.py` -> `prompts.answer_question`). Result: wiki-grounded answers with zero backend.
- **Phase 3 - wire into the real chat UI.** Behind a "run a model in this browser" toggle on `/chat`
  when no backend is reachable. **Reuse the existing chat token-stream rendering** - the server's SSE
  emits `data: {"token": "..."}` / `data: {"meta": {...}}` / `data: [DONE]` (see `chat_stream` in
  `anthill/web/app.py`); have the browser engine yield the **same event shape** so the UI is unchanged.
  Offline-capable via the service worker.
- **Phase 4 - polish.** Model-cache management UI (size, evict), a couple of curated small models, the
  privacy + hardware-ceiling banners, and a one-click public demo deployment.

---

## 4. Invariants - do not break

- **Solo plane only.** In-tab inference never touches team/org (those require the shared backend by
  design). Make that explicit in the UI so no one assumes org data is involved.
- **Privacy is the headline.** Nothing leaves the tab; say so plainly.
- **Hardware ceiling.** Small models only (1-3 B practical) - it is the device, not the approach. Set
  expectations; this is a lite/demo tier, not the full assistant.
- **Inference-only.** No training in the browser.

---

## 5. How to contribute (dev workflow)

- Repo `OneHillAI/Anthill`. Python 3.10/3.11. Branch off `main`.
- Before a PR: `python -m pytest tests/ -q`, `ruff format`, `ruff check`. **Slop gate:** no em/en dashes
  (U+2013/U+2014) in tracked `.md` or `.py` - use plain hyphens (CI fails otherwise).
- Open a PR; CI must pass; squash-merge. See `CONTRIBUTING.md` for the full flow.
- **Verify browser changes manually** in a WebGPU browser - CI cannot run WebGPU or download a model, so
  automated tests cover serving/wiring only. Capture load + first-token numbers in the PR.

---

## 6. Context to read first

- `docs/USING_ANTHILL.md` - what Anthill is and the Solo/Team/Org planes (this feature is Solo).
- `docs/OKGF.md` - the portable wiki bundle format used for Phase 2 grounding.
- `anthill/wiki/okf.py` (`parse_bundle` / `export_bundle`), `anthill/wiki/ask.py` (how the server grounds
  a prompt - mirror its shape client-side), `anthill/web/app.py` (`/lite` + `chat_stream` SSE).
- The fuller design rationale (phasing, trade-offs) lives in the project's engineering plan
  `BROWSER_INFERENCE.md` - ask a maintainer for it if you do not have it.
