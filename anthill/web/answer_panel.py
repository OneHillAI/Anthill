"""What the chat page shows of an answer's record (anthill/wiki/answer_record.py): rows for the panel under each
answer, and the list of items used across a chat. Spec: docs/specs/answer-manifest.md, issue #281."""

from __future__ import annotations

from urllib.parse import quote

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


def panel(stored: str) -> dict:
    """The rows and the count left out of a stored record, for the panel under one answer."""
    rec = answer_record.loads(stored)
    return {"rows": rows(rec), "more": rec["more"]}


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
