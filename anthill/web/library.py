"""The Library: one list of the pages and the original documents of a wiki, with what each is and where it came
from. Spec: docs/specs/library-list.md, issue #312. It only reads: the records (anthill/wiki/items.py) say what
each item is, and a page or document with no record, or a damaged one, is listed all the same, without chips."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from ..common.text import first_h1
from ..wiki import items
from ..wiki.doc_context import original_name

LIFECYCLE_CHIPS = {"superseded": "Replaced", "archived": "Archived", "trashed": "Removed"}
REVIEW_CHIPS = {"proposed": "Waiting for a decision", "rejected": "Rejected"}
UNCHECKED_CHIP = "AI-written, unchecked"


def _query(scope: str, team_id: int | None) -> str:
    return f"?scope={scope}" + (f"&team_id={team_id}" if team_id else "")


def _chips(rec: items.ItemRecord | None) -> list[str]:
    if rec is None:
        return []
    chips = [LIFECYCLE_CHIPS[rec.lifecycle]] if rec.lifecycle in LIFECYCLE_CHIPS else []
    if rec.review_state in REVIEW_CHIPS:
        chips.append(REVIEW_CHIPS[rec.review_state])
    if rec.origin == "model-written" and rec.review_state == "unchecked":
        chips.append(UNCHECKED_CHIP)
    return chips


def _day(rec: items.ItemRecord | None, mtime: float) -> str:
    if rec is not None and rec.learned[:10]:
        return rec.learned[:10]
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def _page_record(text: str) -> items.ItemRecord | None:
    try:
        return items.read_page_record(text)
    except items.RecordError:
        return None


# Reading a page's record parses its front matter, about 2 ms a page. A page is read again only when its file
# changed (its modified time, size, inode or change time), so a wiki of thousands of pages pays that once.
_PAGE_CACHE: dict[str, tuple[tuple, str, items.ItemRecord | None]] = {}
_PAGE_CACHE_MAX = 20_000


def _page_facts(path) -> tuple[str, items.ItemRecord | None, float]:
    """The title, the record and the modified time of a page file."""
    try:
        st = path.stat()
    except OSError:
        return path.stem, None, 0.0
    sig = (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)
    hit = _PAGE_CACHE.get(str(path))
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2], st.st_mtime
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    title, rec = first_h1(text) or path.stem, _page_record(text)
    if len(_PAGE_CACHE) >= _PAGE_CACHE_MAX:
        _PAGE_CACHE.clear()
    _PAGE_CACHE[str(path)] = (sig, title, rec)
    return title, rec, st.st_mtime


def _source_record(ws, name: str) -> items.ItemRecord | None:
    try:
        return items.read_source_record(ws, name)
    except items.RecordError:
        return None


def _page_row(path, title, rec, mtime, made_from, query, scope, team_id) -> dict:
    slug = quote(path.stem)
    chips = _chips(rec)
    return {
        "kind": "page",
        "key": path.stem,
        "record": rec,
        "title": title,
        "href": f"/wiki/page/{slug}{query}",
        "chips": chips,
        "unchecked": UNCHECKED_CHIP in chips,
        "links": made_from,
        "links_label": "Made from",
        "when": _day(rec, mtime),
        "details_href": f"/wiki/item?kind=page&name={slug}" + query.replace("?", "&"),
        "edit_href": f"/wiki/page/{slug}/edit{query}",
        "remove_href": f"/wiki/remove?kind=page&name={slug}&scope={scope}"
        + (f"&team_id={team_id}" if team_id else ""),
    }


def _doc_row(f, rec, pages, query, scope, team_id) -> dict:
    name = quote(f["name"])
    chips = _chips(rec)
    return {
        "kind": "document",
        "key": f["name"],
        "record": rec,
        "title": original_name(f["name"]),
        "href": f"/wiki/raw/{name}{query}",
        "text_href": f"/wiki/text/{name}{query}" if f.get("has_text") else "",
        "chips": chips,
        "unchecked": UNCHECKED_CHIP in chips,
        "links": pages,
        "links_label": "Pages made from it",
        "size": f["size"],
        "when": _day(rec, f["mtime"]),
        "details_href": f"/wiki/item?kind=document&name={name}" + query.replace("?", "&"),
        "edit_href": "",
        "remove_href": f"/wiki/remove?kind=original&name={name}&scope={scope}"
        + (f"&team_id={team_id}" if team_id else ""),
    }


def _made_from(doc_of_id: dict, rec, query) -> list[dict]:
    return [
        {
            "title": original_name(doc_of_id[i]["name"]),
            "href": f"/wiki/raw/{quote(doc_of_id[i]['name'])}{query}",
        }
        for i in (rec.derived_from if rec is not None else [])
        if i in doc_of_id
    ]


def build(ws, raw_files: list[dict], *, scope: str, team_id: int | None = None) -> list[dict]:
    """Every page and original document of ``ws`` as a row, by title. ``raw_files`` is the wiki's list of kept
    originals (name, size, modified time, whether its text was kept)."""
    query = _query(scope, team_id)
    pages = []
    for path in ws.pages():
        title, rec, mtime = _page_facts(path)
        pages.append((path, title, rec, mtime))
    docs = [(f, _source_record(ws, f["name"])) for f in raw_files]

    doc_of_id = {rec.id: f for f, rec in docs if rec is not None}
    pages_of_id: dict[str, list[dict]] = {}
    for path, title, rec, _mtime in pages:
        for source_id in rec.derived_from if rec is not None else []:
            pages_of_id.setdefault(source_id, []).append(
                {"title": title, "href": f"/wiki/page/{quote(path.stem)}{query}"}
            )

    rows = [
        _page_row(path, title, rec, mtime, _made_from(doc_of_id, rec, query), query, scope, team_id)
        for path, title, rec, mtime in pages
    ]
    rows += [
        _doc_row(
            f, rec, pages_of_id.get(rec.id, []) if rec is not None else [], query, scope, team_id
        )
        for f, rec in docs
    ]
    rows.sort(key=lambda r: (r["title"].casefold(), r["kind"]))
    return rows


def row(ws, kind: str, key: str, *, scope: str, team_id: int | None = None) -> dict | None:
    """The row of one item, found directly: ``kind`` is "page" (``key`` is its slug) or "document" (``key`` is
    the name the original is kept under). Only what the row needs is read: the item's own file and record, and,
    for its links, the records of the originals a page names or the pages of a document. None if it is not there."""
    query = _query(scope, team_id)
    if kind == "page":
        path = ws.wiki / f"{key}.md"
        if not path.is_file():
            return None
        title, rec, mtime = _page_facts(path)
        wanted = set(rec.derived_from) if rec is not None else set()
        found: dict[str, dict] = {}
        for name in _raw_names(ws) if wanted else []:
            source = _source_record(ws, name)
            if source is not None and source.id in wanted:
                found[source.id] = {"name": name}
        return _page_row(
            path, title, rec, mtime, _made_from(found, rec, query), query, scope, team_id
        )
    if kind == "document":
        f = _raw_entry(ws, key)
        if f is None:
            return None
        rec = _source_record(ws, key)
        pages = []
        if rec is not None:
            for path in ws.pages():
                title, page_rec, _mtime = _page_facts(path)
                if page_rec is not None and rec.id in page_rec.derived_from:
                    pages.append({"title": title, "href": f"/wiki/page/{quote(path.stem)}{query}"})
        return _doc_row(f, rec, pages, query, scope, team_id)
    return None


def _raw_names(ws) -> list[str]:
    if not ws.raw.is_dir():
        return []
    return sorted(p.name for p in ws.raw.iterdir() if p.is_file() and not p.name.startswith("."))


def _raw_entry(ws, name: str) -> dict | None:
    """The kept original called ``name`` as the Library lists it, or None."""
    path = ws.raw / name
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != ws.raw.resolve():
            return None
        st = path.stat()
    except OSError:
        return None
    text = ws.text / f"{name}.txt"
    try:
        has_text = (
            text.is_file() and not text.is_symlink() and text.resolve().parent == ws.text.resolve()
        )
    except OSError:
        has_text = False
    return {"name": name, "size": st.st_size, "mtime": st.st_mtime, "has_text": has_text}


def filtered(
    rows: list[dict], *, kind: str = "", unchecked: bool = False, q: str = ""
) -> list[dict]:
    """The rows that match the filters: ``kind`` is "pages" or "documents" (anything else is all), ``unchecked``
    keeps only AI-written pages nobody has checked, ``q`` is part of the title."""
    wanted = {"pages": "page", "documents": "document"}.get(kind, "")
    needle = q.strip().casefold()
    return [
        r
        for r in rows
        if (not wanted or r["kind"] == wanted)
        and (not unchecked or r["unchecked"])
        and (not needle or needle in r["title"].casefold())
    ]
