"""Spot an upload that would duplicate or overwrite a page already in the wiki (#149).

Ingest stores every source once under a content hash and tags the page it writes with that source
name, so a page can be traced back to the file it came from. Before an uploaded page is filed, it is
compared with what the wiki already holds. A hit never writes anything: the upload is held in the
review queue with a proposal from Anthill, and the person decides (keep the existing page, replace
it, or keep both).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..common.text import first_h1, slugify
from ..lifecycle.version import read_provenance

# ingest names the stored source "<stem>-<first 12 hex of sha256><suffix>"
_SOURCE_RE = re.compile(r"^(?P<stem>.+)-(?P<hash>[0-9a-f]{12})(?P<ext>\.[^.]*)$")

# Review flags for a held upload; the value is the choice Anthill recommends.
RECOMMENDED = {"same_file": "keep", "new_version": "replace", "title_clash": "both"}
HELD_FLAGS = frozenset(RECOMMENDED)

_ADVICE = {
    "keep": "Recommended: keep the existing page and discard this copy.",
    "replace": "Recommended: replace the existing page with this newer version.",
    "both": "Recommended: keep both, with this one under a new title.",
}


def _source(content: str) -> tuple[str, str]:
    """(source name without its hash, hash) of the file a page was ingested from, else ("", "")."""
    m = _SOURCE_RE.match((read_provenance(content) or {}).get("source", ""))
    return (m["stem"] + m["ext"], m["hash"]) if m else ("", "")


def _added(page, content: str) -> str:
    stamp = (read_provenance(content) or {}).get("date")
    return stamp or datetime.fromtimestamp(page.stat().st_mtime, timezone.utc).date().isoformat()


def upload_conflict(ws, slug: str, content: str) -> dict | None:
    """The existing page that ``content`` (an ingested page, ready to file under ``slug``) duplicates
    or would overwrite, as ``{flag, slug, proposal}``; None when it is genuinely new.

    Same source hash: the same file again, whatever title the model gave it. Otherwise a page already
    at ``slug``: a different file with the same title, or a newer version of a file with the same
    name."""
    name, digest = _source(content)
    hit = None
    for page in ws.pages():
        text = page.read_text()
        if digest and _source(text)[1] == digest:
            hit, flag = page, "same_file"
            break
    else:
        page = ws.wiki / f"{slug}.md"
        if not page.exists():
            return None
        text = page.read_text()
        hit, flag = page, "new_version" if name and _source(text)[0] == name else "title_clash"
    title = first_h1(text) or hit.stem
    when = _added(hit, text)
    reason = {
        "same_file": f"This looks like the same file as '{title}' (added {when}), "
        "which is already in this wiki.",
        "new_version": f"'{title}' (added {when}) came from a file with this name, "
        "and this upload has different content.",
        "title_clash": f"A page called '{title}' (added {when}) already exists "
        "and was made from a different file.",
    }[flag]
    return {"flag": flag, "slug": hit.stem, "proposal": f"{reason} {_ADVICE[RECOMMENDED[flag]]}"}


def distinct_title(ws, title: str) -> str:
    """``title`` with the upload date appended (and a counter if that is taken too), so a kept-both
    page never lands on an existing slug."""
    base = f"{title} (uploaded {date.today().isoformat()})"
    candidate, n = base, 2
    while (ws.wiki / f"{slugify(candidate)}.md").exists():
        candidate, n = f"{base} {n}", n + 1
    return candidate


def retitle(content: str, title: str) -> str:
    """``content`` with its first H1 replaced by ``title`` (or given one if it has none)."""
    if re.search(r"(?m)^# .*$", content):
        return re.sub(r"(?m)^# .*$", lambda _: f"# {title}", content, count=1)
    return f"# {title}\n\n{content}"
