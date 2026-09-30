"""Memory layer - distil durable facts from chats/tasks and recall them later.

The automatic tier between the semantic cache (exact-ish Q→A reuse) and the wiki
(curated, review-gated knowledge): short, durable items the system pulls out of
everyday use and feeds back into future answers, so it gets smarter without anyone
manually marking snippets. Personal-first; promotion to org scope is governed
elsewhere (corroboration / admin), like training.

This module is engine-level and dependency-light: extraction takes any backend
with a .chat(); recall is a pure ranking over (id, text, vector) tuples. Embeddings
are best-effort - if the embedder is unavailable, storage still works and recall
falls back to keyword overlap.
"""

from __future__ import annotations

import base64
import json
import re

import numpy as np

from .inference.base import Message

MAX_ITEMS = 5

_EXTRACT_SYS = (
    "You extract DURABLE memory from a conversation: facts, decisions, and stable "
    "preferences that would be useful to remember in future, unrelated chats. "
    "Rules:\n"
    "- Only lasting things (a decision made, a preference, a stable fact about the "
    "user or org). NOT pleasantries, one-off questions, or anything ephemeral.\n"
    "- Each item is one short self-contained sentence, understandable with no context.\n"
    f"- At most {MAX_ITEMS} items. If nothing is worth remembering, return an empty list.\n"
    'Return ONLY a JSON array of strings, e.g. ["X uses Postgres for billing.", "X prefers metric units."]'
)


def extract(text: str, backend, *, max_items: int = MAX_ITEMS) -> list[str]:
    """Pull durable memory items from text using the model. [] on nothing/failure."""
    try:
        raw = backend.chat([Message("system", _EXTRACT_SYS), Message("user", text[:6000])])
    except Exception:
        return []
    return _parse_items(raw, max_items)


def _parse_items(raw: str, max_items: int) -> list[str]:
    raw = (raw or "").strip()
    items: list[str] = []
    # Prefer a JSON array anywhere in the response.
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
            items = [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            items = []
    if not items:  # fallback: bullet/numbered lines
        for line in raw.splitlines():
            s = line.strip().lstrip("-*0123456789. ").strip()
            if len(s) > 3 and not s.startswith(("[", "]", "{", "}")):
                items.append(s)
    # de-dupe within the batch, cap length
    seen, out = set(), []
    for it in items:
        key = _norm(it)
        if key and key not in seen:
            seen.add(key)
            out.append(it[:500])
        if len(out) >= max_items:
            break
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def is_new(text: str, existing: list[str]) -> bool:
    """Cheap, embedding-free de-dup: reject exact/normalised duplicates of memory
    we already hold (substring either direction)."""
    n = _norm(text)
    if not n:
        return False
    for e in existing:
        en = _norm(e)
        if n == en or (len(n) > 12 and (n in en or en in n)):
            return False
    return True


# ── embedding helpers (best-effort; degrade to keyword recall) ─────────────────


def embed_text(text: str):
    """Return an np.ndarray embedding, or None if the embedder isn't available."""
    try:
        from .cache import embedder as emb

        return emb.embed(text)
    except Exception:
        return None


def encode_vec(vec) -> str:
    if vec is None:
        return ""
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode()


def decode_vec(s: str):
    if not s:
        return None
    try:
        return np.frombuffer(base64.b64decode(s), dtype=np.float32)
    except Exception:
        return None


def cosine(a, b) -> float:
    """Cosine similarity of two vectors. 0.0 if either is missing or shapes differ."""
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.shape != b.shape or a.size == 0:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def is_semantically_new(vec, existing_vecs, *, threshold: float = 0.92) -> bool:
    """True unless `vec` is a near-duplicate (cosine >= threshold) of an existing
    vector. With no query vector this defers to the caller (returns True) so the
    cheap text de-dup (`is_new`) stays the gate when embeddings are unavailable."""
    if vec is None:
        return True
    return all(cosine(vec, ev) < threshold for ev in existing_vecs if ev is not None)


def recall(items, query: str, query_vec=None, *, k: int = 3, min_score: float = 0.30) -> list:
    """Rank memory items against a query and return the top-k.

    items: iterable of (id, text, embedding_b64). Uses cosine when a query vector
    and stored vectors are available; otherwise keyword overlap. Returns the same
    tuples (id, text, score) sorted best-first.
    """
    scored = []
    use_vec = query_vec is not None
    q_tokens = set(_norm(query).split()) if query else set()
    for item_id, text, emb_b64 in items:
        score = 0.0
        vec = decode_vec(emb_b64) if use_vec else None
        if use_vec and vec is not None and vec.shape == np.asarray(query_vec).shape:
            score = float(np.dot(query_vec, vec))  # both L2-normalised → cosine
        elif q_tokens:  # keyword fallback
            t = set(_norm(text).split())
            score = len(q_tokens & t) / max(1, len(q_tokens))
        if score >= min_score:
            scored.append((item_id, text, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:k]
