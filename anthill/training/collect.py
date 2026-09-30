from __future__ import annotations

from sqlalchemy.orm import Session

from ..web.db import TrainingExample


def record_example(
    db: Session,
    *,
    instruction: str,
    output: str,
    context: str = "",
    quality: str = "bronze",
    model: str = "",
    task_type: str = "general",
    source: str = "chat",
    org_id: int | None = None,
    user_id: int | None = None,
    scope: str = "personal",
    training_eligible: bool = True,
    council_drafts: str = "",
) -> TrainingExample:
    """Record one Q&A pair as a training example.

    Called automatically after every generated answer. Quality starts at bronze
    and is promoted by signals. scope is 'personal' by default - only corroborated
    or review-approved examples become 'org' and train the shared model.

    ``training_eligible`` is False when a contributing model was reached through a third-party
    closed-model API whose own terms restrict using its outputs to train another model - captured
    regardless (for audit/context), but excluded from a later fine-tuning export. ``council_drafts``
    is a JSON array of {"member_index", "text"} - each surviving council member's own draft, banked
    alongside the flattened final ``output`` when the answer was council-synthesized; "" otherwise.
    """
    ex = TrainingExample(
        org_id=org_id,
        user_id=user_id,
        instruction=instruction,
        context=context[:4000],  # cap context to avoid huge rows
        output=output,
        quality=quality,
        scope=scope,
        model=model,
        task_type=task_type,
        source=source,
        training_eligible=training_eligible,
        council_drafts=council_drafts,
    )
    db.add(ex)
    db.commit()
    return ex


def promote_to_gold(db: Session, example_id: int) -> None:
    """Promote to gold - called when an admin approves a wiki page,
    or when a user gives a thumbs-up in the chat UI."""
    ex = db.query(TrainingExample).filter(TrainingExample.id == example_id).first()
    if ex:
        ex.quality = "gold"
        db.commit()


def promote_to_silver(db: Session, instruction: str, org_id: int | None) -> None:
    """Promote the most recent matching bronze example to silver.

    Called when the org index returns this answer to a *different* node -
    meaning it was useful enough that someone else benefited from it. That
    cross-user reuse is itself corroboration, so silver examples are org-scope.
    """
    ex = (
        db.query(TrainingExample)
        .filter(
            TrainingExample.instruction == instruction,
            TrainingExample.quality == "bronze",
            TrainingExample.org_id == org_id,
        )
        .order_by(TrainingExample.created_at.desc())
        .first()
    )
    if ex:
        ex.quality = "silver"
        ex.scope = "org"  # reuse by another user = corroboration
        db.commit()
