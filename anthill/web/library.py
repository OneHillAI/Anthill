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

    rows: list[dict] = []
    for path, title, rec, mtime in pages:
        slug = quote(path.stem)
        made_from = [
            {
                "title": original_name(doc_of_id[i]["name"]),
                "href": f"/wiki/raw/{quote(doc_of_id[i]['name'])}{query}",
            }
            for i in (rec.derived_from if rec is not None else [])
            if i in doc_of_id
        ]
        rows.append(
            {
                "kind": "page",
                "key": path.stem,
                "record": rec,
                "title": title,
                "href": f"/wiki/page/{slug}{query}",
                "chips": _chips(rec),
                "unchecked": UNCHECKED_CHIP in _chips(rec),
                "links": made_from,
                "links_label": "Made from",
                "when": _day(rec, mtime),
                "details_href": f"/wiki/item?kind=page&name={slug}" + query.replace("?", "&"),
                "edit_href": f"/wiki/page/{slug}/edit{query}",
                "remove_href": f"/wiki/remove?kind=page&name={slug}&scope={scope}"
                + (f"&team_id={team_id}" if team_id else ""),
            }
        )
    for f, rec in docs:
        name = quote(f["name"])
        rows.append(
            {
                "kind": "document",
                "key": f["name"],
                "record": rec,
                "title": original_name(f["name"]),
                "href": f"/wiki/raw/{name}{query}",
                "text_href": f"/wiki/text/{name}{query}" if f.get("has_text") else "",
                "chips": _chips(rec),
                "unchecked": UNCHECKED_CHIP in _chips(rec),
                "links": pages_of_id.get(rec.id, []) if rec is not None else [],
                "links_label": "Pages made from it",
                "size": f["size"],
                "when": _day(rec, f["mtime"]),
                "details_href": f"/wiki/item?kind=document&name={name}" + query.replace("?", "&"),
                "edit_href": "",
                "remove_href": f"/wiki/remove?kind=original&name={name}&scope={scope}"
                + (f"&team_id={team_id}" if team_id else ""),
            }
        )
    rows.sort(key=lambda r: (r["title"].casefold(), r["kind"]))
    return rows


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
