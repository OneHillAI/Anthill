"""What the chat page shows of an answer's record (anthill/wiki/answer_record.py): rows for the panel under each
answer, and the list of items used across a chat. Spec: docs/specs/answer-manifest.md, issue #281."""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

from ..wiki import answer_record

SPACE_LABELS = {"personal": "Your wiki", "team": "Team wiki", "org": "Organisation wiki"}


def _scope_query(space: str, space_ref: str) -> str:
    query = f"?scope={space if space in SPACE_LABELS else 'personal'}"
    team_id = space_ref.removeprefix("team-") if space == "team" else ""
    return query + (f"&team_id={team_id}" if team_id.isdigit() else "")


def _row(item: dict) -> dict:
    space, ref = str(item.get("space") or "personal"), str(item.get("space_ref") or "")
    query = _scope_query(space, ref)
    document = item.get("kind") == "document"
    if document:
        href = f"/wiki/text/{quote(str(item.get('name') or ''))}{query}"
    else:
        href = f"/wiki/page/{quote(str(item.get('slug') or ''))}{query}"
    pages = [int(n) for n in item.get("pages") or [] if isinstance(n, int)]
    return {
        "document": document,
        "title": str(item.get("title") or item.get("slug") or item.get("name") or ""),
        "href": href,
        "space": SPACE_LABELS.get(space, space),
        "version": int(item.get("version") or 0),
        "pages": ", ".join(str(n) for n in pages),
        "unchecked": item.get("origin") == "model-written" and item.get("review") == "unchecked",
        "key": (space, ref, str(item.get("kind")), str(item.get("slug") or item.get("name"))),
    }


def rows(record: dict) -> list[dict]:
    return [_row(i) for i in record.get("items", []) if isinstance(i, dict)]


# What the panel says of the other blocks a record holds (rules, profile, ...). The record keeps a type, a hash and a
# length for each and never the text, so a line names the kind of thing and nothing more. A kind not listed here is
# not shown.
OTHER_BLOCKS = (
    ("rules", "Your rules", "The principles that applied to this answer."),
    ("profile", "Your profile", "What Anthill knows about you from your profile."),
    (
        "memory",
        "Notes Anthill remembered",
        "Remembered notes that were added to this answer's prompt.",
    ),
    (
        "context",
        "The context this run started with",
        "What an agent run was given before it began.",
    ),
)
ROUTE_WORDS = {"chat": "in chat", "agent": "in agent mode"}


def _web_link(entry: dict) -> dict:
    """A page from the web the answer was given: a link only for an http or https address; anything else is text."""
    address = str(entry.get("url") or "")[:500]
    parts = urlsplit(address)
    safe = parts.scheme in ("http", "https") and bool(parts.netloc)
    return {"url": address, "host": parts.netloc if safe else "", "link": safe}


def other_blocks(blocks: list[dict]) -> dict:
    """The lines of the "Also given" list and the web pages, from the non-item blocks of a record."""
    kinds = {str(b.get("type")) for b in blocks}
    lines = [
        {"label": label, "title": title} for kind, label, title in OTHER_BLOCKS if kind in kinds
    ]
    turns = sum(1 for b in blocks if b.get("type") == "history_turn")
    if turns:
        lines.append(
            {
                "label": f"{turns} earlier message{'' if turns == 1 else 's'} in this chat",
                "title": "Earlier turns of this conversation that went into the prompt.",
            }
        )
    web = [_web_link(b) for b in blocks if b.get("type") == "web_evidence" and b.get("url")]
    return {"also": lines, "web": web}


def _answered_by(stored: str) -> str:
    """ "Answered by <model>, in chat" from the route and model the record carries, or an empty text."""
    try:
        raw = json.loads(stored) if stored else {}
    except ValueError:
        return ""
    if not isinstance(raw, dict):
        return ""
    model = str(raw.get("model") or "").strip()[:80]
    route = ROUTE_WORDS.get(str(raw.get("route") or ""), "")
    if not model:
        return ""
    return f"Answered by {model}{', ' + route if route else ''}"


def panel(stored: str) -> dict:
    """The rows and the count left out of a stored record, and the other things the answer was given, for the panel
    under one answer."""
    rec = answer_record.loads(stored)
    return {
        "rows": rows(rec),
        "more": rec["more"],
        **other_blocks(rec.get("blocks", [])),
        "by": _answered_by(stored),
    }


def used_in_chat(messages) -> list[dict]:
    """Every item the answers of a chat were given, once each, with how many answers used it."""
    seen: dict[tuple, dict] = {}
    for msg in messages:
        if getattr(msg, "role", "") != "assistant":
            continue
        for row in rows(answer_record.loads(getattr(msg, "manifest", "") or "")):
            entry = seen.setdefault(row["key"], {**row, "answers": 0})
            entry["answers"] += 1
    return sorted(seen.values(), key=lambda r: (-r["answers"], r["title"].lower()))
