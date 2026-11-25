"""How a page file is written and what is kept of it. Spec: docs/specs/one-page-write.md.

``Workspace.write_page`` is the one place a page is written, and it goes through here:

- The file is replaced in one step (a temporary file in the same folder, then a rename), so a reader and a crash
  see the old page or the new one, never half of one.
- Writes to one page take turns. A save made from a copy of the page that has since changed is refused
  (``StaleSave``), not applied over the newer text.
- The version a write replaces is kept in ``history/<slug>/``, a whole page file as it was, record included. The newest
  ``MAX_VERSIONS`` are kept; older ones are deleted.
- A page file whose text no longer matches the hash in its record was changed outside the app. That is noticed
  here, recorded as a person's edit, and never overwritten by anything but a person's save made from that text.

Nothing here calls a model."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from ..common.text import strip_frontmatter
from . import items, okf
from .doc_context import original_name

if TYPE_CHECKING:
    from .workspace import Workspace

HISTORY_DIR = "history"
RESTORED_KEY = (
    "x-anthill-restored-from"  # front matter of a version made by restoring an earlier one
)
MAX_VERSIONS = 50  # earlier versions kept of one page; the oldest go first
_VERSION_FILE = re.compile(r"^(\d{4,})-([0-9a-f]{12})\.md$")


class StaleSave(Exception):
    """A write was refused because the page is not the text the writer started from. ``outside`` is True when the
    page was changed outside the app (and has just been recorded as a person's edit), False when another save
    got there first."""

    def __init__(self, slug: str, *, outside: bool = False):
        self.slug = slug
        self.outside = outside
        super().__init__(
            f"page {slug!r} was changed outside the app since it was last saved"
            if outside
            else f"page {slug!r} was changed after this copy of it was read"
        )


_locks: dict[str, list] = {}  # page file -> [its lock, how many writers hold or wait for it]
_locks_guard = threading.Lock()


@contextmanager
def page_lock(path: Path):
    """Writes to one page file take turns (within this process, which is the one that writes pages). A page's lock
    exists only while a write holds or waits for it."""
    key = str(path)
    with _locks_guard:
        entry = _locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _locks[key]


def page_hash(text: str) -> str:
    """The hash of a page as a reader sees it, the same one the record holds (``items.body_hash``). An edit form
    carries it, and a save is refused when the page no longer has it."""
    return items.body_hash(text)


def atomic_write(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one step. The temporary file is made beside it (so the rename stays on
    one filesystem) and is removed if anything fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def history_dir(ws: Workspace, slug: str) -> Path:
    return ws.root / HISTORY_DIR / slug


def keep_previous(ws: Workspace, slug: str, text: str) -> Path | None:
    """Keep ``text`` (a whole page file) as the next earlier version of the page. Not kept again when it is the
    version kept last."""
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    kept = versions(ws, slug)
    if kept and _VERSION_FILE.match(kept[-1][1].name).group(2) == digest:  # type: ignore[union-attr]
        return kept[-1][1]
    dest = history_dir(ws, slug) / f"{(kept[-1][0] if kept else 0) + 1:04d}-{digest}.md"
    atomic_write(dest, text)
    for _n, old in kept[: max(len(kept) + 1 - MAX_VERSIONS, 0)]:
        old.unlink(missing_ok=True)
    return dest


def outside_edit(text: str) -> items.ItemRecord | None:
    """The record of a page file whose text no longer matches the hash its record holds: it was changed outside
    the app. None for a page that matches, has no record or has a damaged one (those are the inventory's)."""
    try:
        rec = items.read_page_record(text)
    except items.RecordError:
        return None
    if rec is None or not rec.content_hash or rec.content_hash == page_hash(text):
        return None
    return rec


def record_as_person_edit(rec: items.ItemRecord, text: str) -> str:
    """The page file ``text`` with its record brought up to date: a new version, written by a person, checked. The
    text itself is not touched."""
    page = okf.parse_okf(text)
    rec.parent_hash = rec.content_hash
    rec.content_hash = page_hash(text)
    rec.version += 1
    rec.origin, rec.review_state = "person-edited", "checked"
    page.item = rec.validate().to_dict()
    return okf.to_okf(page)


def prepare(ws: Workspace, path: Path, *, base_hash: str | None, by_person: bool) -> str | None:
    """Look at the page file about to be replaced, under its lock. Returns its text (None for a new page) after
    recording an outside edit in it, or raises ``StaleSave``:

    - the writer started from text that is not the page's now (``base_hash`` given and different), or
    - the page was changed outside the app and the writer is not a person saving from that text."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    outside = False
    rec = outside_edit(text)
    if rec is not None:
        text = record_as_person_edit(rec, text)
        atomic_write(path, text)
        outside = True
    if base_hash is not None and page_hash(text) != base_hash:
        raise StaleSave(path.stem, outside=outside)
    if outside and not by_person:
        raise StaleSave(path.stem, outside=True)
    return text


def versions(ws: Workspace, slug: str) -> list[tuple[int, Path]]:
    """The kept earlier versions of a page, oldest first: (number, file)."""
    folder = history_dir(ws, slug)
    out = []
    for p in folder.glob("*.md") if folder.is_dir() else ():
        m = _VERSION_FILE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def version_text(ws: Workspace, slug: str, number: int) -> str | None:
    """The whole file of kept version ``number`` of a page, or None when there is no such version."""
    for n, path in versions(ws, slug):
        if n == number:
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
    return None


def describe(text: str) -> dict:
    """What a person is told about a kept version: the day and time it was written and who wrote its text."""
    page = okf.parse_okf(text)
    try:
        rec = items.read_page_record(text)
    except items.RecordError:
        rec = None
    origin = rec.origin if rec is not None else "unknown"
    restored = page.extra.get(RESTORED_KEY)
    if isinstance(restored, int) and not isinstance(restored, bool):
        by = f"restored from version {restored}"
    elif origin == "person-edited":
        by = "by a person"
    elif origin == "model-written":
        by = "as an AI summary"
    elif origin == "own":
        source = items.source_name_of(text)
        by = f"as uploaded from {original_name(source)}" if source else "as uploaded"
    elif origin == "imported":
        by = "by an import"
    else:
        by = "(author not recorded)"
    stamp = page.timestamp
    return {"day": stamp[:10], "time": stamp[11:16], "by": by}


def changed(older: str | None, newer: str) -> str:
    """One short line on what a version changed from the one before it: how many lines were added and removed."""
    if older is None:
        return "first kept version"
    a, b = _lines(older), _lines(newer)
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            removed += i2 - i1
            added += j2 - j1
    parts = [f"{added} line{'s' if added != 1 else ''} added"] if added else []
    if removed:
        parts.append(f"{removed} line{'s' if removed != 1 else ''} removed")
    return ", ".join(parts) or "no change in the text"


def _lines(text: str) -> list[str]:
    body = strip_frontmatter(text)
    return [
        ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("<!--")
    ]
