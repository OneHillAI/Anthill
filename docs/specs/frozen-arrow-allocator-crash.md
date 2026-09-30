# Spec: chat must not crash the packaged sidecar (Arrow allocator)

Status: implemented. Lane: `pillar:platform`.

## Problem

In the packaged desktop app every chat turn ended with "(connection lost)". The frozen sidecar
(`anthill-server`, PyInstaller one-file) was killed by SIGSEGV (exit status -11) the moment a chat
reached the semantic cache. The same code run from source answered normally. Earlier releases did not
show it because the cache stayed keyword-only until an embedding model was present; once the bundled
Ollama had pulled `bge-m3` (#769) every chat exercised the native lancedb/pyarrow path.

## Root cause

A native stack captured from a locally built sidecar (a preloaded SIGSEGV handler) shows a null
dereference (address `0x18`) in `mi_thread_init`, inside `libarrow.2500.dylib`, reached from
`pyarrow.Table.from_pylist` while lancedb converts the row to add, on a worker thread. pyarrow 25 uses
mimalloc as Arrow's default allocator, and its per-thread setup crashes the first time a new thread
allocates inside the frozen binary.

Ruled out by experiment: code signing and entitlements (an unsigned local build crashes identically),
notarization, and dependency drift (the hash-pinned lock and the sidecar spec did not change between
v0.11.19 and v0.12.4). A frozen probe that runs the cache on the main thread also passes, which is why
this survived a naive smoke test.

The pyarrow patch version matters, but only inside the frozen build. With the unfixed launch tree and
every other pin from the shipped lock unchanged, a sidecar frozen with pyarrow 25.0.0 (what the lock
pins) is killed by SIGSEGV on the real chat every time it was tried (more than ten runs), while one
frozen with pyarrow 25.0.1 answers it (4 of 4 runs). The same lock run unfrozen works with either
version. That also explains why a fresh, unpinned build did not reproduce the crash. The allocator
default below makes the sidecar safe on either version, so bumping the lock to pyarrow 25.0.1 or later
is a second layer, left to a follow-up.

Confirmed fix: the same frozen sidecar with `ARROW_DEFAULT_MEMORY_POOL=system` answers a full chat.

## Requirements

### R1 - System allocator by default

- Importing `anthill` SHALL default `ARROW_DEFAULT_MEMORY_POOL` to `system` before pyarrow's first
  allocation, in every entry point (sidecar, CLI, web).
- An operator who sets `ARROW_DEFAULT_MEMORY_POOL` SHALL keep control (`setdefault`).

### R2 - Release gate

- The sidecar SHALL accept `--selfcheck-cache`. It SHALL exit 1 unless the frozen binary is on the
  `system` Arrow pool, and it SHALL add and search one row through the real `CacheStore` on a worker
  thread with lancedb imported there. A native crash exits with a signal.
- `scripts/smoke_frozen_chat.py` SHALL boot the frozen binary on a throwaway database against a small
  fake Ollama (no model or network needed), sign up, run one chat, and require a real streamed answer
  and a sidecar that is still running.
- `scripts/build-sidecar.sh` SHALL run both against `dist/anthill-server` and fail the build otherwise.

### R3 - Graceful degrade

- `SemanticCache` SHALL degrade to a no-op (lookup returns nothing, store does nothing) when its store
  cannot open, and when `ANTHILL_DISABLE_SEMANTIC_CACHE` is truthy, so chat still answers keyword-only.
- A hard native crash cannot be caught in-process. This requirement covers Python-level failures only;
  R1 prevents the crash and R2 keeps it from shipping.

### R4 - Actionable error copy

- When the chat stream drops before any token arrives, the message SHALL tell the user what to do
  ("You stepped on one of us. Please retry, and if it doesn't work, quit and reopen Anthill.") instead
  of "(connection lost)".

## Acceptance criteria

- A fresh interpreter reports the `system` Arrow pool after `import anthill`, and `mimalloc` when the
  operator overrides it.
- `anthill-server --selfcheck-cache` exits 0 on the frozen binary, and exits 1 with a clear message when
  the allocator is forced back to `mimalloc`.
- `scripts/smoke_frozen_chat.py dist/anthill-server` passes on the frozen binary.
- The packaged sidecar answers a chat end to end against a real local model (verified: the v0.12.4
  sidecar exits with SIGSEGV on the same chat; the fixed build answers in full).
- `SemanticCache` returns no hit and raises nothing when its store cannot open or the kill switch is set.

## Limits of the gate

The crash itself could not be reproduced offline. Against the shipped, crashing v0.12.4 sidecar, every
hermetic imitation passed: a main-thread and a worker-thread cache probe, importing lancedb on the
worker, importing the whole app first, running inside an asyncio loop on the worker, a slow fake
embedder, and the fault handler on and off. Only a chat through a real Ollama crashes it. So the
allocator assertion in `--selfcheck-cache` is what guards this fix, and it fails whenever the fix is
lost. `scripts/smoke_frozen_chat.py` guards the wider class of a dead frozen chat (routes, templates,
bundled data, boot), but on its own it would NOT have caught this bug. A follow-up can run the same
probe against a real Ollama on the release runner if that proves worth the cost.

## Considered and not done

- A subprocess probe that tests the native stack before each open would survive a crash, but in a
  one-file PyInstaller build every probe re-extracts the whole bundle. With the cause removed and a
  build gate in place it is not worth that cost.
