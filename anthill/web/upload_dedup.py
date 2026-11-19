"""Spot an upload that would duplicate or overwrite a page already in the wiki (#149).

Ingest stores every source once under a content hash and tags the page it writes with that source
name, so a page can be traced back to the file it came from. Before an uploaded page is filed, it is
compared with what the wiki already holds. A hit never writes anything: the upload is held in the
review queue with a proposal from Anthill, and the person decides (keep the existing page, replace
it, or keep both).
"""

from __future__ import annotations

import difflib
import re
from datetime import date, datetime, timezone

from ..common.text import first_h1, slugify
from ..lifecycle.version import read_provenance

# ingest names the stored source "<stem>-<first 12 hex of sha256><suffix>"
_SOURCE_RE = re.compile(r"^(?P<stem>.+)-(?P<hash>[0-9a-f]{12})(?P<ext>\.[^.]*)$")

# Review flags for a held upload; the value is the choice Anthill recommends ("" when it cannot tell).
RECOMMENDED = {
    "same_file": "keep",
    "new_version": "replace",
    "title_clash": "both",
    "looks_newer": "",
}
HELD_FLAGS = frozenset(RECOMMENDED)

_ADVICE = {
    "keep": "Recommended: keep the existing page and discard this copy.",
    "replace": "Recommended: replace the existing page with this newer version.",
    "both": "Recommended: keep both, with this one under a new title.",
}


# A page whose title differs from the upload's only by numbers or version words ("Pricing 2025" and "Pricing
# 2026", "Plan v1" and "Plan v2", "Report final" and "Report") and whose text is mostly the same may be an older
# version of it. The person is asked; nothing is decided from the names alone.
_VERSION_WORDS = re.compile(r"\b(final|draft|copy|new|old|updated|revised|version|rev|latest)\b")
_SIMILAR_MIN = 0.6
_COMPARE_CHARS = 8000


def _stem(title: str) -> str:
    """``title`` reduced to its letters, without numbers, version markers or dates."""
    text = _VERSION_WORDS.sub(" ", re.sub(r"\([^)]*\)", " ", title.lower()))
    return " ".join(re.findall(r"[a-z]+", re.sub(r"\bv\d+\b", " ", text)))


def _lines(content: str) -> list[str]:
    """The readable lines of a page, without its front matter or Anthill's trailing provenance comment."""
    body = re.sub(
        r"<!--.*?-->", "", re.sub(r"\A---\n.*?\n---\n", "", content, flags=re.S), flags=re.S
    )
    return [ln.strip() for ln in body.splitlines() if ln.strip()]


def _body(content: str) -> str:
    """The text of a page without its title line, so a shared title does not make two pages look alike."""
    lines = _lines(content)
    return " ".join(lines[1:] if lines and lines[0].startswith("#") else lines)[:_COMPARE_CHARS]


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
            return _similar_conflict(ws, slug, content)
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


def _similar_conflict(ws, slug: str, content: str) -> dict | None:
    """A page that this upload may be a newer version of: same title apart from numbers and version
    words, and mostly the same text. ``{flag, slug, proposal}``, or None. Cheap and deterministic: it
    only decides whether to ASK, never what the answer is."""
    stem = _stem(first_h1(content) or slug.replace("-", " "))
    if len(stem) < 3:
        return None
    new_text = _body(content)
    best = None
    for page in ws.pages():
        text = page.read_text()
        if _stem(first_h1(text) or page.stem) != stem:
            continue
        ratio = difflib.SequenceMatcher(None, _body(text), new_text, autojunk=False).ratio()
        if ratio >= _SIMILAR_MIN and (best is None or ratio > best[0]):
            best = (ratio, page, text)
    if best is None:
        return None
    _, page, text = best
    title = first_h1(text) or page.stem
    return {
        "flag": "looks_newer",
        "slug": page.stem,
        "proposal": f"This looks like a newer version of '{title}' (added {_added(page, text)}). "
        "If it is, replace the old page with it; if they are different documents, keep both.",
    }


def _marked(old: str, new: str) -> tuple[list, list]:
    """``old`` and ``new`` as lists of ``(changed, text)`` runs of words, so the changed words can be marked."""
    a, b = old.split(), new.split()
    before: list = []
    after: list = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if i2 > i1:
            before.append((tag != "equal", " ".join(a[i1:i2])))
        if j2 > j1:
            after.append((tag != "equal", " ".join(b[j1:j2])))
    return before, after


def compare_with_existing(ws, slug: str, new_content: str, limit: int = 10) -> dict | None:
    """What differs between the page now at ``slug`` and ``new_content``, for the person deciding:
    ``{title, added, rows: [{old, new}], more}`` (changed passages only; ``old`` and ``new`` are lists of
    ``(changed, text)`` runs), or None when there is no page."""
    page = ws.wiki / f"{slug}.md"
    if not page.exists():
        return None
    old = page.read_text()
    old_lines, new_lines = _lines(old), _lines(new_content)
    rows = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if tag != "equal":
            before, after = _marked(" ".join(old_lines[i1:i2]), " ".join(new_lines[j1:j2]))
            rows.append({"old": before, "new": after})
    return {
        "title": first_h1(old) or page.stem,
        "added": _added(page, old),
        "rows": rows[:limit],
        "more": max(0, len(rows) - limit),
    }
