"""Item records for sources and pages. Spec: docs/specs/item-records-and-inventory.md; step 1, slice 1a of the
knowledge base update (OneHillAI/Anthill#270, #272).

Every source (an original in ``raw/``) and every page (``wiki/<slug>.md``) gets a small **record**: an
immutable random id, a kind, the space it lives in, its origin, a review state and a lifecycle state (kept
apart), a version with its parent version's hash, the sources it was made from, and a content hash.

- A page's record is the ``x-anthill-item`` key of its front matter, so it travels with the file. ``Workspace.
  write_page`` keeps it across saves (``record_for_save``).
- A source's record is a sidecar ``records/sources/<stored name>.json``. It is not kept inside ``raw/``: the
  Files tab and ``renovate`` both read every file there and would treat a sidecar as a document.

Nothing here changes what an answer reads. The records are only written, read and reported on (see
``inventory``); a later slice makes retrieval follow their state. Writing a record is best effort and never
raises: a failure to keep a record must not fail an upload or a page save.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..common.text import strip_frontmatter

if TYPE_CHECKING:  # workspace imports this module lazily; only the type is needed here
    from .workspace import Workspace

log = logging.getLogger(__name__)

FRONT_MATTER_KEY = "x-anthill-item"
RECORDS_DIR = "records"
SOURCES_DIR = "sources"

KINDS = ("source", "page")
ORIGINS = ("own", "model-written", "person-edited", "imported", "unknown")
REVIEW_STATES = ("proposed", "unchecked", "checked", "rejected")
LIFECYCLE_STATES = ("active", "superseded", "archived", "trashed", "purged")

RETRIEVABLE_REVIEW = ("checked", "unchecked")

_SPACE_REF = re.compile(r"^(user|team|org)-\d+$")
_BOM = chr(0xFEFF)  # written as a number so no invisible character sits in this file
_FRONT = re.compile(rf"\A({_BOM}?)---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class RecordError(ValueError):
    """A record that is present but cannot be trusted (it does not parse, or has an impossible value)."""


@dataclass
class ItemRecord:
    kind: str
    space: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    space_ref: str = (
        ""  # "team-5", "user-2", "org-1": which workspace, so ids stay meaningful off the machine
    )
    name: str = ""  # a source's stored name in raw/, or a page's slug
    origin: str = "unknown"
    review_state: str = "unchecked"
    lifecycle: str = "active"
    version: int = 1
    parent_hash: str = ""  # the content hash of the version this one replaced ("" for the first)
    learned: str = ""  # when Anthill first held it, ISO 8601, recorded automatically
    content_hash: str = (
        ""  # sha256 of a page's body (front matter stripped) or of an original's bytes
    )
    derived_from: list[str] = field(default_factory=list)  # ids of the sources a page was made from

    def validate(self) -> ItemRecord:
        if not isinstance(self.id, str) or not self.id.strip():
            raise RecordError("record has no id")
        for value, allowed, label in (
            (self.kind, KINDS, "kind"),
            (self.origin, ORIGINS, "origin"),
            (self.review_state, REVIEW_STATES, "review state"),
            (self.lifecycle, LIFECYCLE_STATES, "lifecycle state"),
        ):
            if value not in allowed:
                raise RecordError(f"record has an unknown {label}: {value!r}")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise RecordError(f"record has a bad version: {self.version!r}")
        if not isinstance(self.derived_from, list):
            raise RecordError("record's derived_from is not a list")
        return self

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "kind": self.kind,
            "space": self.space,
        }
        if self.space_ref:
            d["space_ref"] = self.space_ref
        if self.name:
            d["name"] = self.name
        d.update(
            origin=self.origin,
            review_state=self.review_state,
            lifecycle=self.lifecycle,
            version=self.version,
            parent_hash=self.parent_hash,
            learned=self.learned,
            content_hash=self.content_hash,
        )
        if self.derived_from:
            d["derived_from"] = list(self.derived_from)
        return d

    @classmethod
    def from_dict(cls, data: object) -> ItemRecord:
        if not isinstance(data, dict):
            raise RecordError("record is not a mapping")
        links = data.get("derived_from") or []
        if not isinstance(links, list) or not all(isinstance(x, str) for x in links):
            raise RecordError("record's derived_from is not a list of ids")
        try:
            rec = cls(
                kind=str(data.get("kind", "")),
                space=str(data.get("space", "")),
                id=data.get("id") or "",
                space_ref=str(data.get("space_ref") or ""),
                name=str(data.get("name") or ""),
                origin=str(data.get("origin", "")),
                review_state=str(data.get("review_state", "")),
                lifecycle=str(data.get("lifecycle", "")),
                version=data.get("version", 1),
                parent_hash=str(data.get("parent_hash") or ""),
                learned=str(data.get("learned") or ""),
                content_hash=str(data.get("content_hash") or ""),
                derived_from=list(links),
            )
        except (TypeError, ValueError) as exc:
            raise RecordError(f"record does not parse: {exc}") from exc
        return rec.validate()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def body_hash(text: str) -> str:
    """sha256 of a page's body: front matter stripped, surrounding whitespace trimmed. The same definition
    everywhere, so a hash taken at save time and one taken at inventory time can be compared."""
    return hashlib.sha256(strip_frontmatter(text).strip().encode("utf-8", "replace")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_front_matter(text: str) -> tuple[str | None, str]:
    """(front matter YAML text, everything after the closing delimiter exactly as it is). ``(None, text)`` when
    the text has no front matter. Never changes a byte of the rest: the inventory relies on that."""
    m = _FRONT.match(text)
    if not m:
        return None, text
    return m.group(2), text[m.end() :]


def read_page_record(text: str) -> ItemRecord | None:
    """The record in a page's front matter, or None when it has none. Raises ``RecordError`` when the front
    matter does not parse or the record is damaged."""
    front, _rest = split_front_matter(text)
    if front is None:
        return None
    try:
        fm = yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        raise RecordError(f"front matter does not parse: {exc.__class__.__name__}") from exc
    if not isinstance(fm, dict):
        raise RecordError("front matter is not a mapping")
    if FRONT_MATTER_KEY not in fm:
        return None
    return ItemRecord.from_dict(fm[FRONT_MATTER_KEY])


def workspace_space(ws: Workspace) -> tuple[str, str]:
    """(space, space_ref) of a workspace: today's scope name and the folder it is held in."""
    ref = ws.root.name if _SPACE_REF.match(ws.root.name) else ""
    return (ws.scope or "personal"), ref


def origin_from_markdown(markdown: str) -> str:
    """What the provenance comment says about who wrote a page: a note filed as uploaded is the person's own
    text, a tagged page was written by a model, anything else is unknown."""
    from ..lifecycle.version import VERBATIM_MODEL, read_provenance

    prov = read_provenance(markdown)
    if prov is None:
        return "unknown"
    return "own" if prov["model"] == VERBATIM_MODEL else "model-written"


def initial_review_state(origin: str, review: str = "") -> str:
    if review == "approved" or origin == "own":
        return "checked"
    return "unchecked"


def source_name_of(markdown: str) -> str:
    from ..lifecycle.version import read_provenance

    prov = read_provenance(markdown) or {}
    name = str(prov.get("source") or "")
    return name if name and Path(name).name == name else ""


# ── sources ───────────────────────────────────────────────────────────────────


def source_record_path(ws: Workspace, name: str) -> Path:
    if not name or Path(name).name != name or name.startswith("."):
        raise RecordError(f"not a stored original's name: {name!r}")
    return ws.root / RECORDS_DIR / SOURCES_DIR / f"{name}.json"


def read_source_record(ws: Workspace, name: str) -> ItemRecord | None:
    path = source_record_path(ws, name)
    if not path.is_file():
        return None
    try:
        return ItemRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise RecordError(f"source record does not parse: {exc.__class__.__name__}") from exc


def write_source_record(ws: Workspace, rec: ItemRecord) -> Path:
    path = source_record_path(ws, rec.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def new_source_record(
    ws: Workspace, raw: Path, *, origin: str = "own", review_state: str = "proposed"
):
    space, ref = workspace_space(ws)
    return ItemRecord(
        kind="source",
        space=space,
        space_ref=ref,
        name=raw.name,
        origin=origin,
        review_state=review_state,
        learned=now_iso(),
        content_hash=file_hash(raw),
    )


def ensure_source_record(
    ws: Workspace, raw: Path, *, origin: str = "own", reopen: bool = False
) -> ItemRecord | None:
    """The record of a kept original, made if it has none. "proposed" until a page made from it lands (see
    ``mark_source_checked``). With ``reopen`` (a new upload of the same file) a rejected original becomes
    "proposed" again: it is a new proposal. Best effort: returns None when the original is missing, the
    existing record is damaged (it is never overwritten), or the record cannot be written."""
    try:
        existing = read_source_record(ws, raw.name)
        if existing is not None:
            if reopen and existing.review_state == "rejected":
                existing.review_state = "proposed"
                write_source_record(ws, existing)
            return existing
        if not raw.is_file():
            return None
        rec = new_source_record(ws, raw, origin=origin)
        write_source_record(ws, rec)
        return rec
    except (OSError, RecordError) as exc:
        log.warning("could not keep the record of %s: %s", raw.name, exc)
        return None


def mark_source_checked(ws: Workspace, name: str) -> None:
    """A page made from this original has landed in the workspace, so the original is approved. Best effort."""
    try:
        rec = read_source_record(ws, name)
        if rec is not None and rec.review_state in ("proposed", "rejected"):
            rec.review_state = "checked"
            write_source_record(ws, rec)
    except (OSError, RecordError) as exc:
        log.warning("could not update the record of %s: %s", name, exc)


def mark_source_rejected(ws: Workspace, name: str) -> bool:
    """The upload that proposed this original was rejected. The original is marked rejected only when no page
    in the workspace was made from it: the same file uploaded twice is one original, and rejecting the second
    copy must not take away the first one's. Returns True when it was marked. Best effort."""
    try:
        for page in ws.pages():
            if source_name_of(page.read_text(encoding="utf-8")) == name:
                return False
        rec = read_source_record(ws, name)
        if rec is None or rec.review_state == "rejected":
            return False
        rec.review_state = "rejected"
        write_source_record(ws, rec)
        return True
    except (OSError, UnicodeDecodeError, RecordError) as exc:
        log.warning("could not update the record of %s: %s", name, exc)
        return False


# ── the one rule: what an answer may read ─────────────────────────────────────


def is_retrievable(rec: ItemRecord | None) -> bool:
    """May an answer read this item? Only when it has a valid record and the record says active and approved:
    lifecycle ``active`` and review state ``checked`` or ``unchecked``. Waiting (``proposed``), rejected,
    superseded, archived, trashed and purged items are not, and neither is an item with no record: a missing
    record never means "allowed". A wiki made before records existed is given its records once, automatically
    (``inventory.ensure_upgraded``), so it keeps working."""
    if rec is None:
        return False
    return rec.lifecycle == "active" and rec.review_state in RETRIEVABLE_REVIEW


_ITEM_BLOCK = re.compile(r"^x-anthill-item:[ \t]*\r?\n((?:[ \t]+\S.*\r?\n?)+)", re.M)
_LIFECYCLE = re.compile(r"^[ \t]+lifecycle:[ \t]*(\S+)[ \t]*$", re.M)
_REVIEW = re.compile(r"^[ \t]+review_state:[ \t]*(\S+)[ \t]*$", re.M)
_PAGE_HEAD_BYTES = 8192


def _plainly_retrievable(front: str) -> bool:
    """True when the record's two state lines, as this module writes them, say active and approved. A cheap
    look that spares parsing every page's YAML on the first question; anything else is judged by a full parse."""
    block = _ITEM_BLOCK.search(front)
    if block is None:
        return False
    life, review = _LIFECYCLE.search(block.group(1)), _REVIEW.search(block.group(1))
    return bool(
        life and review and life.group(1) == "active" and review.group(1) in RETRIEVABLE_REVIEW
    )


_page_memo: dict[str, tuple[tuple[int, int], bool]] = {}
_MEMO_LIMIT = 50_000


def _page_head(path: Path) -> str:
    """The start of a page, enough to hold its front matter; the whole page when the front matter is longer."""
    with path.open("rb") as fh:
        head = fh.read(_PAGE_HEAD_BYTES)
        if len(head) == _PAGE_HEAD_BYTES and not _FRONT.match(head.decode("utf-8", "replace")):
            head += fh.read()
    return head.decode("utf-8", "replace")


def page_file_retrievable(path: Path) -> bool:
    """May an answer read this page file? Judged from its record in the front matter, and remembered until the
    file changes. A damaged record or an unreadable file is not retrievable: it cannot be shown to be approved."""
    if path.parent.name == "wiki":
        ensure_records(path.parent.parent)
    try:
        st = path.stat()
    except OSError:
        return False
    stamp = (st.st_mtime_ns, st.st_size)
    key = str(path)
    memo = _page_memo.get(key)
    if memo is not None and memo[0] == stamp:
        return memo[1]
    try:
        head = _page_head(path)
        front, _rest = split_front_matter(head)
        if front is None or FRONT_MATTER_KEY not in front:
            ok = False  # no record: not retrievable
        else:
            ok = _plainly_retrievable(front) or is_retrievable(read_page_record(head))
    except (OSError, RecordError):
        ok = False
    if len(_page_memo) >= _MEMO_LIMIT:
        _page_memo.clear()
    _page_memo[key] = (stamp, ok)
    return ok


_sources_memo: dict[str, tuple[tuple[int, int], frozenset[str]]] = {}
_upgrade_tried: set[str] = set()
_upgrade_lock = threading.Lock()


def ensure_records(root: Path) -> None:
    """Give the workspace at ``root`` its records, once per process, before the first answer reads it (see
    ``inventory.ensure_upgraded``). The app does it with the review rows at start-up; this is the same step for a
    command line or a library caller. A folder that is not a workspace is left alone."""
    key = str(root)
    if key in _upgrade_tried:
        return
    with (
        _upgrade_lock
    ):  # a second question waits for the first to finish, so it never sees a half-done wiki
        if key in _upgrade_tried:
            return
        from . import inventory
        from .workspace import workspace_at

        inventory.ensure_upgraded(workspace_at(root))
        _upgrade_tried.add(key)


def upgrade_notice(root: Path) -> str:
    """What to tell the person about the automatic upgrade of the workspace at ``root``: why it could not run,
    or, once, that it ran. Empty when there is nothing to say."""
    from . import inventory

    err = inventory.UPGRADE_ERRORS.get(str(root))
    if err:
        return (
            "Anthill could not prepare this wiki, so pages and documents made before this version are not used "
            f"in answers. Reason: {err}. It will try again at the next start."
        )
    marker = root / RECORDS_DIR / inventory.UPGRADE_MARKER
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
        if info.get("seen") or not (info.get("pages") or info.get("sources")):
            return ""
        info["seen"] = True
        marker.write_text(json.dumps(info) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        return ""
    undo = (
        f" To undo it, run: anthill knowledge inventory --undo records/{info['manifest']}"
        if info.get("manifest")
        else ""
    )
    return (
        f"Anthill prepared this wiki: {info.get('pages', 0)} pages and {info.get('sources', 0)} documents now "
        f"have records, and no page text changed. A backup was made first.{undo}"
    )


def hidden_sources(root: Path) -> frozenset[str]:
    """The stored names of the originals in the workspace at ``root`` that an answer may not read: those with no
    record, whose record says waiting, rejected, trashed and so on, or whose record is damaged. Remembered until
    a record or the folder of originals changes."""
    ensure_records(root)
    folder = root / RECORDS_DIR / SOURCES_DIR
    raw = root / "raw"

    def _mtime(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0

    stamp = (_mtime(folder), _mtime(raw))
    key = str(folder)
    memo = _sources_memo.get(key)
    if memo is not None and memo[0] == stamp:
        return memo[1]
    hidden: set[str] = set()
    try:
        recorded = {p.name[: -len(".json")] for p in folder.glob("*.json")}
        hidden |= {
            p.name for p in raw.iterdir() if p.is_file() and not p.name.startswith(".")
        } - recorded
    except OSError:
        pass  # no folder of originals: nothing to hide
    for sidecar in folder.glob("*.json"):
        name = sidecar.name[: -len(".json")]
        try:
            rec = ItemRecord.from_dict(json.loads(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            hidden.add(name)
            continue
        if not is_retrievable(rec):
            hidden.add(name)
    out = frozenset(hidden)
    _sources_memo[key] = (stamp, out)
    return out


# ── pages ─────────────────────────────────────────────────────────────────────


def record_for_save(
    ws: Workspace, path: Path, body: str, *, review: str = "", by_person: bool = False
) -> dict:
    """The ``x-anthill-item`` mapping to write into a page that is being saved at ``path`` with ``body``.

    A page that already has a record keeps its id, origin, review state, lifecycle state and links; a body that
    changed bumps the version and remembers the hash it replaced. A page with no record gets one. ``by_person`` says
    a person wrote this text: the page's origin becomes ``person-edited`` and it counts as checked. Never raises:
    on any problem it returns an empty mapping and the page is saved without a record."""
    try:
        new_hash = hashlib.sha256(body.strip().encode("utf-8", "replace")).hexdigest()
        existing: ItemRecord | None = None
        if path.is_file():
            try:
                existing = read_page_record(path.read_text(encoding="utf-8"))
            except (OSError, RecordError):
                existing = None  # a damaged record is not carried forward as if it were good
        source = source_name_of(body)
        links: list[str] = []
        if source:
            src = ensure_source_record(ws, ws.raw / source)
            links = [src.id] if src else [f"name:{source}"]
            if src:
                mark_source_checked(ws, source)
        if existing is not None:
            rec = existing
            if new_hash != rec.content_hash:
                rec.version += 1
                rec.parent_hash = rec.content_hash
                rec.content_hash = new_hash
                if by_person:
                    rec.origin, rec.review_state = "person-edited", "checked"
            if not rec.derived_from:
                rec.derived_from = links
        else:
            origin = origin_from_markdown(body)
            space, ref = workspace_space(ws)
            rec = ItemRecord(
                kind="page",
                space=space,
                space_ref=ref,
                name=path.stem,
                origin="person-edited" if by_person else origin,
                review_state="checked" if by_person else initial_review_state(origin, review),
                learned=now_iso(),
                content_hash=new_hash,
                derived_from=links,
            )
        return rec.validate().to_dict()
    except Exception as exc:  # noqa: BLE001 - a record must never be the reason a page is not saved
        log.warning("could not make the record of %s: %s", path.name, exc)
        return {}


def is_person_edited(markdown: str) -> bool:
    """True for a page whose record says a person wrote its text. A page with no record, or a damaged one, is not."""
    try:
        rec = read_page_record(markdown)
    except RecordError:
        return False
    return rec is not None and rec.origin == "person-edited"


# ── reading a workspace ───────────────────────────────────────────────────────


@dataclass
class Quarantined:
    path: str
    reason: str


@dataclass
class WorkspaceItems:
    pages: list[ItemRecord] = field(default_factory=list)
    sources: list[ItemRecord] = field(default_factory=list)
    unrecorded_pages: list[str] = field(default_factory=list)  # slugs with no record yet
    unrecorded_sources: list[str] = field(default_factory=list)  # stored names with no record yet
    quarantined: list[Quarantined] = field(default_factory=list)
    page_count: int = 0  # every page file, including a quarantined one
    raw_names: list[str] = field(
        default_factory=list
    )  # every kept original, including a quarantined one


def _scan_pages(ws: Workspace, out: WorkspaceItems) -> None:
    out.page_count = len(ws.pages())
    for page in ws.pages():
        try:
            rec = read_page_record(page.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            out.quarantined.append(
                Quarantined(f"wiki/{page.name}", f"unreadable: {exc.__class__.__name__}")
            )
        except RecordError as exc:
            out.quarantined.append(Quarantined(f"wiki/{page.name}", str(exc)))
        else:
            if rec is None:
                out.unrecorded_pages.append(page.stem)
            else:
                out.pages.append(rec)


def _scan_sources(ws: Workspace, out: WorkspaceItems) -> None:
    if ws.raw.is_dir():
        out.raw_names = sorted(
            p.name for p in ws.raw.iterdir() if p.is_file() and not p.name.startswith(".")
        )
    for name in out.raw_names:
        try:
            rec = read_source_record(ws, name)
        except RecordError as exc:
            out.quarantined.append(
                Quarantined(f"{RECORDS_DIR}/{SOURCES_DIR}/{name}.json", str(exc))
            )
        else:
            if rec is None:
                out.unrecorded_sources.append(name)
            else:
                out.sources.append(rec)


def _scan_sidecars(ws: Workspace, out: WorkspaceItems) -> None:
    """A sidecar whose original is gone is quarantined, not silently kept."""
    sidecars = ws.root / RECORDS_DIR / SOURCES_DIR
    if not sidecars.is_dir():
        return
    known = set(out.raw_names)
    for sidecar in sorted(sidecars.glob("*.json")):
        if sidecar.name[: -len(".json")] not in known:
            out.quarantined.append(
                Quarantined(
                    f"{RECORDS_DIR}/{SOURCES_DIR}/{sidecar.name}",
                    "record of an original that is gone",
                )
            )


def list_items(ws: Workspace) -> WorkspaceItems:
    """Every record in a workspace, read from its files, and what has no record or has a damaged one. Reads
    only; the database is not involved, which is what lets the records survive losing it."""
    out = WorkspaceItems()
    _scan_pages(ws, out)
    _scan_sources(ws, out)
    _scan_sidecars(ws, out)
    return out


__all__ = [
    "FRONT_MATTER_KEY",
    "ItemRecord",
    "Quarantined",
    "RecordError",
    "WorkspaceItems",
    "body_hash",
    "ensure_source_record",
    "file_hash",
    "hidden_sources",
    "is_retrievable",
    "list_items",
    "mark_source_checked",
    "mark_source_rejected",
    "page_file_retrievable",
    "read_page_record",
    "read_source_record",
    "record_for_save",
    "split_front_matter",
]
