"""Snippet service: save a marked piece of content, derive its 'red line',
and feed it into the knowledge + training layers - at the right SCOPE.

Trust boundary (mirrors personal-wiki → org-wiki promotion):
  • A user saving/tagging a snippet is the strongest signal **for that user**.
    It becomes a PERSONAL gold example - improves that user's experience,
    but does NOT train the shared org model and does NOT enter the org KB.
  • It is promoted to ORG scope only when corroborated: ≥ N distinct users
    deliberately save matching content, OR an admin approves it into the wiki.
  • Org-scope examples are what the shared model trains on (see export.py).

Filing to the org wiki stays a separate, review-gated action.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy.orm import Session

from ..inference.base import Message
from .db import Snippet, Team, TeamMembership, TrainingExample

# How many DISTINCT users must deliberately save matching content before the
# signal counts for the whole org. The original saver + at least one other.
ORG_PROMOTE_DISTINCT_USERS = 2


def content_key(text: str) -> str:
    """Stable key for corroboration: normalised (lowercased, whitespace-collapsed)."""
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()


def parse_chat_ref(source_ref: str):
    """Parse a chat snippet's source_ref ("conv:42/msg:7") into (conversation_id, message_id).

    Returns (None, None) for a non-chat ref (e.g. "task:3") or an unparseable value, so a
    snippet promoted to the wiki can carry a provenance link back to the exact turn it came from."""
    conv_id = msg_id = None
    for part in (source_ref or "").split("/"):
        key, _, val = part.partition(":")
        if key == "conv" and val.isdigit():
            conv_id = int(val)
        elif key == "msg" and val.isdigit():
            msg_id = int(val)
    return conv_id, msg_id


def derive_red_line(question: str, content: str, tags: str, *, backend) -> str:
    """Ask the model why this snippet matters. Returns '' on any failure."""
    sys = (
        "A user saved the text below as worth keeping. In ONE or two sentences, "
        "state the 'red line' - the throughline: why this is relevant and worth "
        "learning from, connecting it to the question that produced it. "
        "Be specific and concrete. No preamble."
    )
    parts = []
    if question:
        parts.append(f"QUESTION THAT PRODUCED IT:\n{question}")
    if tags:
        parts.append(f"USER TAGS: {tags}")
    parts.append(f"SAVED SNIPPET:\n{content}")
    try:
        return backend.chat([Message("system", sys), Message("user", "\n\n".join(parts))]).strip()
    except Exception:
        return ""


def save_snippet(
    db: Session,
    *,
    org_id: int | None,
    user_id: int | None,
    content: str,
    question: str = "",
    tags: str = "",
    source: str = "chat",
    source_ref: str = "",
    backend=None,
    org_threshold: int = ORG_PROMOTE_DISTINCT_USERS,
) -> Snippet:
    """Persist a snippet (PERSONAL gold), then promote to ORG if corroborated."""
    content = (content or "").strip()
    tags = ",".join(t.strip() for t in tags.split(",") if t.strip())
    key = content_key(content)

    rationale = ""
    if backend is not None and content:
        rationale = derive_red_line(question, content, tags, backend=backend)

    snip = Snippet(
        org_id=org_id,
        user_id=user_id,
        content=content,
        question=question,
        tags=tags,
        rationale=rationale,
        source=source,
        source_ref=source_ref,
        content_key=key,
        scope="personal",
    )
    db.add(snip)
    db.flush()  # snip.id

    # A saved snippet is gold - but PERSONAL until others corroborate it.
    ex = TrainingExample(
        org_id=org_id,
        user_id=user_id,
        instruction=question or "Useful reference saved by a user.",
        context=rationale,  # the red line rides along as context
        output=content,
        quality="gold",
        scope="personal",
        source="snippet",
        task_type="reference",
    )
    db.add(ex)
    db.flush()
    snip.training_id = ex.id

    # Corroboration: which DISTINCT users saved matching content in this org?
    savers = {
        uid
        for (uid,) in db.query(Snippet.user_id)
        .filter(Snippet.org_id == org_id, Snippet.content_key == key)
        .distinct()
        .all()
        if uid is not None
    }
    if len(savers) >= org_threshold:
        # Localized to a single team -> team scope; spanning beyond it -> org scope.
        # (No teams, or savers split across teams, falls through to org - the old
        # behaviour, so a plain two-user corroboration still promotes to org.)
        localized_team = None
        for (tid,) in db.query(Team.id).filter(Team.org_id == org_id).all():
            if savers <= _team_member_ids(db, tid):
                localized_team = tid
                break
        if localized_team is not None:
            _promote_key(db, org_id, key, "team", team_id=localized_team)
            snip.scope, snip.team_id = "team", localized_team
        else:
            _promote_key(db, org_id, key, "org")
            snip.scope = "org"

    db.commit()
    return snip


def _team_member_ids(db: Session, team_id: int) -> set:
    """Active member user-ids of a team."""
    return {
        uid
        for (uid,) in db.query(TeamMembership.user_id)
        .filter(TeamMembership.team_id == team_id, TeamMembership.status == "active")
        .distinct()
        .all()
    }


def _promote_key(
    db: Session, org_id: int | None, key: str, scope: str, team_id: int | None = None
) -> int:
    """Promote every snippet (and its training example) with this content_key to
    the given scope (and team, if any). Returns how many training examples changed."""
    snips = db.query(Snippet).filter(Snippet.org_id == org_id, Snippet.content_key == key).all()
    n = 0
    for s in snips:
        s.scope = scope
        s.team_id = team_id
        if s.training_id:
            ex = db.query(TrainingExample).filter(TrainingExample.id == s.training_id).first()
            if ex and (ex.scope != scope or ex.team_id != team_id):
                ex.scope = scope
                ex.team_id = team_id
                n += 1
    return n


def promote_snippet(db: Session, snippet: Snippet, scope: str, team_id: int | None = None) -> None:
    """Promote a snippet (and matching ones) to a target scope - used on wiki approval."""
    _promote_key(db, snippet.org_id, snippet.content_key, scope, team_id=team_id)
    db.commit()


def all_tags(db: Session, org_id: int | None) -> list[str]:
    rows = db.query(Snippet).filter(Snippet.org_id == org_id).all()
    tags: set[str] = set()
    for r in rows:
        tags.update(t for t in r.tags.split(",") if t)
    return sorted(tags)
