"""Optional Meilisearch retrieval over the org's *own* wiki pages.

Opt-in and self-hosted: when MEILI_URL is set, wiki retrieval queries a local
Meilisearch instance (fast hybrid/typo-tolerant full-text) instead of the naive
embedding-cosine scan. The data never leaves the perimeter - you run the engine.
Every call is best-effort: on any failure the caller falls back to embeddings,
so a down/misconfigured Meilisearch never breaks answering.

  MEILI_URL       e.g. http://localhost:7700
  MEILI_API_KEY   (or MEILI_MASTER_KEY) - optional; omit if the instance is keyless
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from ..common.text import first_h1, strip_frontmatter

INDEX = "anthill-wiki"


def enabled() -> bool:
    return bool(os.environ.get("MEILI_URL"))


def _base() -> str:
    return os.environ["MEILI_URL"].rstrip("/")


def _headers() -> dict:
    key = os.environ.get("MEILI_API_KEY") or os.environ.get("MEILI_MASTER_KEY")
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def index_pages(pages: list[Path], *, index: str = INDEX) -> bool:
    """Upsert wiki pages as searchable documents (id = slug). Best-effort."""
    if not enabled() or not pages:
        return False
    docs = []
    for p in pages:
        try:
            text = p.read_text()
        except Exception:
            continue
        docs.append(
            {"id": p.stem, "title": first_h1(text) or p.stem, "content": strip_frontmatter(text)}
        )
    if not docs:
        return False
    try:
        resp = httpx.post(
            f"{_base()}/indexes/{index}/documents", headers=_headers(), json=docs, timeout=15
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def search_slugs(query: str, k: int = 3, *, index: str = INDEX) -> list[str]:
    """Return up to k page slugs ranked by Meilisearch. [] on failure/disabled."""
    if not enabled():
        return []
    try:
        resp = httpx.post(
            f"{_base()}/indexes/{index}/search",
            headers=_headers(),
            json={"q": query, "limit": k},
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", []) or []
    except Exception:
        return []
    return [h["id"] for h in hits if isinstance(h, dict) and h.get("id")]
