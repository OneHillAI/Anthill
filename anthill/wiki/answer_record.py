"""The record of which items an answer was given. Spec: docs/specs/answer-manifest.md, issue #281.

An answer is given pages of the wiki and passages of the originals. This turns exactly those into a small JSON
record kept with the answer: for each item its id, version, content hash, space and title, and for a document its
stored name and the pages the passages came from. Never the text. It only records: nothing here changes what the
model is given, and no model is called.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..common.text import first_h1
from . import items
from .doc_context import DOC_PREFIX, original_name, stored_name
from .doc_index import Hit

log = logging.getLogger(__name__)

MAX_ITEMS = 50  # a record names at most this many items, then says how many more there were


def _space_of(ws) -> dict:
    space, ref = items.workspace_space(ws)
    return {"space": space, "space_ref": ref}


def _record_fields(rec: items.ItemRecord | None) -> dict:
    if rec is None:
        return {"id": "", "version": 0, "hash": "", "origin": "", "review": ""}
    return {
        "id": rec.id,
        "version": rec.version,
        "hash": rec.content_hash,
        "origin": rec.origin,
        "review": rec.review_state,
    }


def _page_item(path: Path, ws) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    try:
        rec = items.read_page_record(text)
    except items.RecordError:
        rec = None  # a damaged record is reported by the inventory; here the page is simply listed
    return {
        "kind": "page",
        "slug": path.stem,
        "title": first_h1(text) or path.stem,
        **_space_of(ws),
        **_record_fields(rec),
    }


def _document_item(name: str, pages: list[int], ws) -> dict:
    try:
        rec = items.read_source_record(ws, name)
    except items.RecordError:
        rec = None
    return {
        "kind": "document",
        "name": name,
        "title": original_name(name),
        "pages": sorted(pages),
        **_space_of(ws),
        **_record_fields(rec),
    }


def _workspace_of_page(path: Path, spaces):
    for w in spaces:
        if w is not None and path.parent == w.wiki:
            return w
    return None


def _workspace_of_text(name: str, spaces):
    for w in spaces:
        if w is not None and (w.root / "text" / f"{name}.txt").is_file():
            return w
    return None


def _cap(listed: list[dict]) -> dict:
    return {
        "v": 1,
        "items": listed[:MAX_ITEMS],
        "more": max(len(listed) - MAX_ITEMS, 0),
    }


def build(pages, doc_hits: list[Hit], spaces) -> dict:
    """The record of a turn from what it used: the retrieved ``pages`` (paths) and the document ``doc_hits``,
    resolved against the ``spaces`` (workspaces) that ground the chat. Documents come first, as in the prompt.
    Never raises: a problem reading one item leaves it out."""
    listed: list[dict] = []
    try:
        by_doc: dict[str, list[int]] = {}
        for h in doc_hits:
            nums = by_doc.setdefault(h.doc, [])
            if h.page and h.page not in nums:
                nums.append(h.page)
        for name, nums in by_doc.items():
            ws = _workspace_of_text(name, spaces)
            if ws is not None:
                listed.append(_document_item(name, nums, ws))
        for path in pages:
            ws = _workspace_of_page(Path(path), spaces)
            if ws is not None:
                listed.append(_page_item(Path(path), ws))
    except Exception as e:  # noqa: BLE001 - a record must never cost the person their answer
        log.debug("answer record skipped: %s", e)
    return _cap(listed)


def from_sources(sources, spaces) -> dict:
    """The record of an answer that was served from the cache: only its list of sources is known (page slugs and
    ``doc:`` entries), so each is looked up in the ``spaces``, the first that holds it. A slug that is in two
    spaces is listed from the first, which is the limit of a record made after the fact."""
    listed: list[dict] = []
    try:
        for s in sources or []:
            s = str(s)
            if s.startswith(DOC_PREFIX):
                name = stored_name(s)
                ws = _workspace_of_text(name, spaces)
                if ws is not None:
                    listed.append(_document_item(name, _pages_of_tag(s), ws))
                continue
            for w in spaces:
                path = None if w is None else w.wiki / f"{s}.md"
                if path is not None and path.is_file():
                    listed.append(_page_item(path, w))
                    break
    except Exception as e:  # noqa: BLE001 - a record must never cost the person their answer
        log.debug("answer record skipped: %s", e)
    return _cap(listed)


def _pages_of_tag(tag: str) -> list[int]:
    """The page numbers in a ``doc:name (page 2, 3)`` entry."""
    if " (page " not in tag:
        return []
    inside = tag.split(" (page ", 1)[1].rstrip(")")
    return [int(n) for n in inside.replace(" ", "").split(",") if n.isdigit()]


def _root_of(item: dict) -> Path | None:
    """The folder of the wiki an item was read from, from the space its record names. None when it cannot be told."""
    from .workspace import workspace_for

    space, ref = str(item.get("space") or "personal"), str(item.get("space_ref") or "")
    num = ref.split("-", 1)[1] if "-" in ref else ""
    n = int(num) if num.isdigit() else None
    try:
        if space == "team" and n is not None:
            return workspace_for("team", team_id=n).root
        if space == "org":
            return workspace_for("org", org_id=n).root if n is not None else None
        return workspace_for("personal", user_id=n).root
    except (ValueError, OSError):
        return None


def still_eligible(item: dict) -> bool:
    """May an answer still read this recorded item? False when its wiki, file or record is gone or no longer says
    active and approved (a removed, waiting or rejected item). Fails closed: what cannot be found is not eligible."""
    root = _root_of(item)
    if root is None:
        return False
    if item.get("kind") == "document":
        name = str(item.get("name") or "")
        return (
            bool(name)
            and (root / "raw" / name).is_file()
            and name not in items.hidden_sources(root)
        )
    slug = str(item.get("slug") or "")
    path = root / "wiki" / f"{slug}.md"
    return bool(slug) and path.is_file() and items.page_file_retrievable(path)


def stale_turns(messages) -> set[int]:
    """The ids of chat messages to leave out when earlier turns are replayed to the model: an answer whose record names an
    item that may no longer be read, and the question it answered. An answer with no record (made before records) is kept."""
    drop: set[int] = set()
    previous_user: int | None = None
    for m in messages:
        role = getattr(m, "role", "")
        if role == "user":
            previous_user = getattr(m, "id", None)
        elif role == "assistant":
            rec = loads(getattr(m, "manifest", "") or "")
            if any(not still_eligible(i) for i in rec["items"] if isinstance(i, dict)):
                drop.add(m.id)
                if previous_user is not None:
                    drop.add(previous_user)
    return drop


_SPACE_ORDER = ("personal", "team", "org")  # narrowest first


def narrowest_space(record: dict) -> str:
    """The narrowest space among the items an answer was given: personal if any item is personal (or there are none),
    else team, else org. What combines items is no wider than the narrowest of them."""
    spaces = {
        str(i.get("space") or "personal") for i in record.get("items", []) if isinstance(i, dict)
    }
    for space in _SPACE_ORDER:
        if space in spaces or not spaces:
            return space
    return "personal"


def training_context(record: dict) -> str:
    """What a training example keeps of the context its answer was written with: the id, version and space of each item
    the answer was given, and the narrowest of those spaces. Never the text of an item. Empty when nothing was given."""
    keep = ("kind", "id", "version", "hash", "space", "space_ref", "name", "slug")
    items_ = [
        {k: i[k] for k in keep if k in i} for i in record.get("items", []) if isinstance(i, dict)
    ]
    if not items_:
        return ""
    return json.dumps(
        {"v": 1, "space": narrowest_space(record), "items": items_}, separators=(",", ":")
    )


def dumps(record: dict) -> str:
    """The text kept with the answer. An empty record is the empty string: nothing was given."""
    return json.dumps(record, separators=(",", ":")) if record.get("items") else ""


def loads(text: str) -> dict:
    """The record kept with an answer, or an empty one for an old answer or a text that does not parse."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"v": 1, "items": [], "more": 0}
    return {"v": 1, "items": data["items"], "more": int(data.get("more") or 0)}
