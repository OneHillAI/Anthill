from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict

import httpx
import numpy as np

# Ollama's own model-library name for BGE-M3 (was "BAAI/bge-m3", the sentence-transformers repo id,
# before this file switched serving stacks - same architecture, same 1024-dim output, confirmed
# against Ollama's model library; only the runtime differs: GGUF/llama.cpp via the already-bundled
# Ollama runtime instead of torch+sentence-transformers, which the packaged desktop app deliberately
# excludes to keep the installer small (Anthill.spec/Anthill-sidecar.spec). See
# docs/specs/ollama-served-embeddings.md for the full "why".
MODEL_NAME = "bge-m3"
DIM = 1024

_available: bool | None = None


def _base_url() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def available() -> bool:
    """Whether Ollama is reachable and the bge-m3 embedding model is pulled.

    Unlike the old sentence-transformers check (a pure Python import, stable for a process's whole
    lifetime), this can change WHILE the process keeps running - Ollama might not be up yet at
    startup, or the model might still be mid-download - so a False result is retried on the next
    call rather than cached forever; a True result IS cached (once the model's there for this
    install, it stays there). Never raises - any connection problem is "not available", matching the
    old function's crash-free contract, so callers still degrade to keyword-only search.
    """
    global _available
    if _available:
        return True
    try:
        resp = httpx.get(f"{_base_url()}/api/tags", timeout=2.0)
        resp.raise_for_status()
        tags = {(m.get("name") or "").split(":")[0] for m in resp.json().get("models", [])}
        _available = MODEL_NAME in tags
    except Exception:
        _available = False
    return _available


def embed(text: str) -> np.ndarray:
    """Return a 1024-dim BGE-M3 dense embedding, L2-normalised.

    Raises if Ollama/the model is unavailable - callers in answer/cache paths should use
    `safe_embed` (or guard with `available()`) so a missing model can't break chat.

    Ollama's `/api/embed` returns raw (non-normalised) vectors, unlike sentence-transformers'
    `normalize_embeddings=True` this replaces - normalised here so `cosine()`'s "already
    L2-normalised, dot product == cosine similarity" assumption keeps holding for every caller.
    """
    resp = httpx.post(
        f"{_base_url()}/api/embed",
        json={"model": MODEL_NAME, "input": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    vec = np.array(resp.json()["embeddings"][0], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


# One chat turn embeds the same question several times: for the cache lookup, for memory recall, for
# retrieval and for the cache write. Each is a call to the embedding model, so the last few results are
# kept for a few minutes and the model is asked once. Only the callers that embed a question use this;
# page embedding does not, so a burst of pages cannot push a question out.
_RECENT_MAX = 16
_RECENT_TTL_SECONDS = 300.0
_recent: OrderedDict[tuple, tuple[float, np.ndarray]] = OrderedDict()
_recent_lock = threading.Lock()


def embed_recent(text: str) -> np.ndarray:
    """`embed`, remembering the last few results. A failure raises as `embed` does and is not remembered.
    The key holds the embed function, so a replaced one (a test double) never shares an entry with the
    real one. Each caller gets its own copy, so changing a returned vector cannot change the next one."""
    key = (embed, _base_url(), MODEL_NAME, text)
    with _recent_lock:
        hit = _recent.get(key)
        if hit is not None and time.monotonic() - hit[0] < _RECENT_TTL_SECONDS:
            _recent.move_to_end(key)
            return hit[1].copy()
    vec = embed(text)
    with _recent_lock:
        _recent[key] = (time.monotonic(), vec)
        _recent.move_to_end(key)
        while len(_recent) > _RECENT_MAX:
            _recent.popitem(last=False)
    return vec.copy()


def safe_embed(text: str) -> np.ndarray | None:
    """Like `embed`, but returns None instead of raising when embeddings are unavailable
    (Ollama unreachable, or the bge-m3 model not pulled yet). Lets the answer and cache paths fall
    back to keyword-only retrieval rather than dead-ending the user."""
    if not available():
        return None
    try:
        return embed_recent(text)
    except Exception:
        return None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    # Both vectors are already L2-normalised, so dot product == cosine similarity.
    return float(np.dot(a, b))
