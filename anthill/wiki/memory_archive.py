"""What Anthill remembered, kept in a file and offered for import onto the core page. Spec: docs/specs/memory-archive.md
(build plan step 2.3, slice c).

The memory rows themselves are read by the web layer (``anthill/web/memory_archive.py``); this module only writes and reads the files in a
person's personal wiki folder and adds chosen lines to the core page. It never deletes or changes a memory row.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import core_page

log = logging.getLogger(__name__)

ARCHIVE_DIR = "archive"
MARKER = ".memory-archived.json"
PREFIX = "memory-"


def _dir(ws) -> Path:
    return ws.root / ARCHIVE_DIR


def archived(ws) -> bool:
    return (_dir(ws) / MARKER).is_file()


def write(ws, rows: list[dict], *, now: datetime | None = None) -> Path:
    """Write the archive of ``rows`` (each: id, text, kind, source, created) as a JSON file and a readable page, then the marker that
    says it was done. Returns the JSON file. Embeddings are never part of a row."""
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    out = _dir(ws)
    out.mkdir(parents=True, exist_ok=True)
    keep = ("id", "text", "kind", "source", "created")
    clean = [{k: r.get(k, "") for k in keep} for r in rows]
    json_path = out / f"{PREFIX}{day}.json"
    json_path.write_text(
        json.dumps({"v": 1, "archived": day, "rows": clean}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# What Anthill remembered, kept on {day}",
        "",
        f"{len(clean)} items. Nothing here was deleted from Anthill's memory.",
        "",
    ]
    lines += [
        f"- ({r['kind'] or 'fact'}, {str(r['created'])[:10]}) {' '.join(str(r['text']).split())}"
        for r in clean
    ]
    (out / f"{PREFIX}{day}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / MARKER).write_text(
        json.dumps({"at": now.isoformat(), "rows": len(clean), "file": json_path.name}) + "\n",
        encoding="utf-8",
    )
    return json_path


def read_rows(ws) -> list[dict]:
    """The rows of the archive that was written, or [] when there is none or it cannot be read."""
    try:
        marker = json.loads((_dir(ws) / MARKER).read_text(encoding="utf-8"))
        data = json.loads((_dir(ws) / marker["file"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return []
    rows = data.get("rows") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def candidates(ws) -> list[dict]:
    """The archived rows not yet on the core page, each with the line it would become and anything sensitive in it."""
    _profile, prefs = core_page.read(ws)
    present = {x.casefold() for x in core_page._lines_of(prefs)}
    out = []
    for row in read_rows(ws):
        line = core_page.one_line(str(row.get("text", "")))
        if not line or line.casefold() in present:
            continue
        out.append({**row, "line": line, "sensitive": core_page.sensitive_kinds(line)})
    return out


def import_rows(ws, ids: list[int]) -> dict:
    """Add the chosen archived rows as one line each under Preferences, in one save as the person's own edit. A row already on the page
    is skipped; rows that do not fit under the cap are named and not added. ``ids`` that are not in the archive are named too."""
    rows = {str(r.get("id")): r for r in read_rows(ws)}
    profile, prefs = core_page.read(ws)
    lines = core_page._lines_of(prefs)
    seen = {x.casefold() for x in lines}
    added: list[int] = []
    present: list[int] = []
    no_room: list[int] = []
    unknown: list[int] = []
    for rid in ids:
        row = rows.get(str(rid))
        line = core_page.one_line(str(row.get("text", ""))) if row else ""
        if not line:
            unknown.append(rid)
        elif line.casefold() in seen:
            present.append(rid)
        elif (
            len(core_page.compose(profile, "\n".join(f"- {x}" for x in [*lines, line])))
            > core_page.CAP_CHARS
        ):
            no_room.append(rid)
        else:
            lines.append(line)
            seen.add(line.casefold())
            added.append(rid)
    if added:
        ws.write_page(
            core_page.SLUG,
            core_page.compose(profile, "\n".join(f"- {x}" for x in lines)),
            by_person=True,
            base_hash=core_page.base_hash(ws) or None,
        )
    return {
        "added": added,
        "present": present,
        "no_room": no_room,
        "unknown": unknown,
        "saved": bool(added),
    }
