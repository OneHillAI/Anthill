from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pyarrow as pa

# v2: embedder.py moved from sentence-transformers (BAAI/bge-m3) to Ollama-served bge-m3 - same
# architecture and dimension (1024), but a different serving stack producing numerically different
# (if closely correlated) vectors, so an old row's stored embedding isn't safely comparable to a new
# query's. Renaming the table is the migration: any pre-existing "cache" table from before this
# change is simply orphaned (harmless, sits unused on disk) rather than mixed with new rows, and a
# correctly-versioned cache builds up fresh from here - no data-loss risk, no migration code needed.
TABLE = "cache_v2"

_SCHEMA = pa.schema(
    [
        pa.field("id", pa.utf8()),
        pa.field("prompt", pa.utf8()),
        pa.field("embedding", pa.list_(pa.float32(), 1024)),
        pa.field("answer", pa.utf8()),
        # JSON-encoded list of wiki slugs that grounded the answer, so a cache HIT can report the
        # same provenance the fresh answer had (an empty list for web/org/ungrounded answers).
        pa.field("slugs", pa.utf8()),
        pa.field("created_at", pa.utf8()),
    ]
)


class CacheRow(NamedTuple):
    id: str
    prompt: str  # stored only locally; never sent to any central index
    embedding: list[float]
    answer: str
    slugs: list[str]  # wiki pages that grounded this answer (provenance)
    created_at: str  # ISO-8601


class CacheStore:
    """LanceDB-backed local cache of (prompt, embedding, answer, slugs) rows."""

    def __init__(self, db_path: Path) -> None:
        import lancedb

        self._db = lancedb.connect(str(db_path))
        self._table = self._open_or_create()

    def _open_or_create(self):
        if TABLE in self._db.table_names():
            tbl = self._db.open_table(TABLE)
            # Backward-compat: a cache written before provenance lacks the `slugs` column. The cache
            # is a disposable performance layer (it repopulates on the next miss), so recreate it
            # rather than run a column migration.
            if "slugs" not in tbl.schema.names:
                self._db.drop_table(TABLE)
                return self._db.create_table(TABLE, schema=_SCHEMA)
            return tbl
        return self._db.create_table(TABLE, schema=_SCHEMA)

    def add(
        self, prompt: str, embedding: np.ndarray, answer: str, slugs: list[str] | None = None
    ) -> None:
        import hashlib

        row_id = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        self._table.add(
            [
                {
                    "id": row_id,
                    "prompt": prompt,
                    "embedding": embedding.tolist(),
                    "answer": answer,
                    "slugs": json.dumps(slugs or []),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        )

    def search(self, embedding: np.ndarray, threshold: float, limit: int = 5) -> list[CacheRow]:
        """Return rows whose embedding is within `threshold` cosine of `embedding`."""
        results = (
            self._table.search(embedding.tolist(), vector_column_name="embedding")
            .metric("cosine")
            .limit(limit)
            .to_list()
        )
        # LanceDB cosine search returns _distance = 1 - cosine_similarity
        rows = []
        for r in results:
            similarity = 1.0 - r.get("_distance", 1.0)
            if similarity >= threshold:
                rows.append(
                    CacheRow(
                        id=r["id"],
                        prompt=r["prompt"],
                        embedding=r["embedding"],
                        answer=r["answer"],
                        slugs=json.loads(r.get("slugs") or "[]"),
                        created_at=r["created_at"],
                    )
                )
        return rows

    def count(self) -> int:
        return self._table.count_rows()
