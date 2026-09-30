from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..web.db import TrainingExample


def export_jsonl(
    db: Session,
    output: Path,
    *,
    min_quality: str = "silver",
    org_id: int | None = None,
    min_output_len: int = 40,
    scope: str = "org",
) -> int:
    """Export training examples to a JSONL file ready for LoRA fine-tuning.

    Format: one JSON object per line, compatible with unsloth / axolotl:
        {"instruction": "...", "input": "<wiki context>", "output": "..."}

    min_quality: "bronze" | "silver" | "gold"
    scope: "org" (default) trains the SHARED model - only corroborated / reviewed
      examples. "personal" or "all" include single-user signals (per-user use).
    """
    quality_rank = {"bronze": 0, "silver": 1, "gold": 2}
    min_rank = quality_rank.get(min_quality, 1)

    query = db.query(TrainingExample)
    if org_id is not None:
        query = query.filter(TrainingExample.org_id == org_id)
    if scope in ("org", "personal"):
        query = query.filter(TrainingExample.scope == scope)
    rows = query.all()

    written = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as f:
        for row in rows:
            if quality_rank.get(row.quality, 0) < min_rank:
                continue
            if len(row.output.strip()) < min_output_len:
                continue
            record = {
                "instruction": row.instruction,
                "input": row.context,
                "output": row.output,
                "metadata": {
                    "quality": row.quality,
                    "model": row.model,
                    "task_type": row.task_type,
                    "source": row.source,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                },
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    return written


def export_stats(db: Session, org_id: int | None = None) -> dict:
    query = db.query(TrainingExample)
    if org_id is not None:
        query = query.filter(TrainingExample.org_id == org_id)
    rows = query.all()

    counts: dict[str, int] = {"bronze": 0, "silver": 0, "gold": 0}
    by_task: dict[str, int] = {}
    org_trainable = 0  # org-scope silver+gold - what the shared model trains on
    personal = 0  # single-user signals, not yet corroborated
    for r in rows:
        counts[r.quality] = counts.get(r.quality, 0) + 1
        by_task[r.task_type] = by_task.get(r.task_type, 0) + 1
        if getattr(r, "scope", "personal") == "org":
            if r.quality in ("silver", "gold"):
                org_trainable += 1
        else:
            personal += 1

    total = len(rows)
    return {
        "total": total,
        "gold": counts["gold"],
        "silver": counts["silver"],
        "bronze": counts["bronze"],
        "org_trainable": org_trainable,  # corroborated/reviewed - trains the org model
        "personal": personal,  # one-user signals, not yet org-wide
        "by_task": by_task,
        "ready_to_train": org_trainable >= 500,
        "note": (
            f"{org_trainable} org-scope (corroborated) examples train the shared model; "
            f"{personal} personal examples await corroboration. 500+ recommended."
        ),
    }
