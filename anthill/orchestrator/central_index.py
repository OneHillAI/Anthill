from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

TABLE = "central_cache"


class CentralIndex:
    """Orchestrator-side semantic cache index.

    Invariant (§7.2): stores embeddings + answers, NEVER prompt text.
    Embeddings are treated as sensitive metadata - same access controls
    as text, per §7.2. The privacy story rests on access control, not
    on "embeddings are opaque."
    """

    def __init__(self, db_path: Path) -> None:
        import lancedb

        db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(db_path))
        self._table = self._open_or_create()

    def _open_or_create(self):
        schema = pa.schema(
            [
                pa.field("id", pa.utf8()),
                pa.field("node_id", pa.utf8()),
                pa.field("embedding", pa.list_(pa.float32(), 1024)),
                pa.field("answer", pa.utf8()),
                pa.field("created_at", pa.utf8()),
            ]
        )
        if TABLE in self._db.table_names():
            return self._db.open_table(TABLE)
        return self._db.create_table(TABLE, schema=schema)

    def publish(self, node_id: str, embedding: list[float], answer: str) -> str:
        row_id = hashlib.sha256((node_id + answer[:64]).encode()).hexdigest()[:16]
        self._table.add(
            [
                {
                    "id": row_id,
                    "node_id": node_id,
                    "embedding": embedding,
                    "answer": answer,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        )
        return row_id

    def search(self, embedding: list[float], threshold: float, limit: int = 5) -> list[dict]:
        """Return matching entries above threshold cosine similarity."""
        results = (
            self._table.search(embedding, vector_column_name="embedding")
            .metric("cosine")
            .limit(limit)
            .to_list()
        )
        hits = []
        for r in results:
            similarity = 1.0 - r.get("_distance", 1.0)
            if similarity >= threshold:
                hits.append(
                    {
                        "id": r["id"],
                        "node_id": r["node_id"],
                        "answer": r["answer"],
                        "similarity": round(similarity, 4),
                    }
                )
        return hits

    def count(self) -> int:
        return self._table.count_rows()
