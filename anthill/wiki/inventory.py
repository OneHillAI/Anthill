"""Inventory of the knowledge items in a workspace, and a backed-up, reversible way to give them records.
Spec: docs/specs/item-records-and-inventory.md; step 1, slice 1a of OneHillAI/Anthill#270 (issue #272).

``inventory`` only reads: it reports what a workspace holds, what has no record, what looks left over from a
held, rejected or failed upload, and what ``apply_records`` would add. ``apply_records`` writes a backup first,
then adds records and never changes a page body. ``undo_records`` puts every changed file back. No model is
called anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..lifecycle.version import PROVENANCE_RE, read_provenance
from . import items
from .items import FRONT_MATTER_KEY, ItemRecord, RecordError
from .workspace import Workspace, space_of_root

_NAME_HASH = re.compile(r"-([0-9a-f]{12})(\.[^./\\]*)?$")
MANIFEST_PREFIX = "inventory-"
BACKUP_DIR = "_backups"


@dataclass
class WorkspaceReport:
    root: str
    space: str
    space_ref: str
    pages: int = 0
    pages_with_record: int = 0
    sources: int = 0
    sources_with_record: int = 0
    texts: int = 0
    passage_index: bool = False
    reviews_known: bool = False
    findings: dict[str, list[str]] = field(default_factory=dict)
    quarantined: list[dict] = field(default_factory=list)
    would_add_pages: list[str] = field(default_factory=list)
    would_add_sources: list[str] = field(default_factory=list)

    def add(self, category: str, line: str) -> None:
        self.findings.setdefault(category, []).append(line)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "space": self.space,
            "space_ref": self.space_ref,
            "counts": {
                "pages": self.pages,
                "pages_with_record": self.pages_with_record,
                "sources": self.sources,
                "sources_with_record": self.sources_with_record,
                "texts": self.texts,
            },
            "passage_index_present": self.passage_index,
            "review_rows_read": self.reviews_known,
            "findings": self.findings,
            "quarantined": self.quarantined,
            "would_add": {"pages": self.would_add_pages, "sources": self.would_add_sources},
        }

    def to_text(self) -> str:
        lines = [
            f"{self.root}  [{self.space}{' ' + self.space_ref if self.space_ref else ''}]",
            f"  pages {self.pages} ({self.pages_with_record} with a record), originals {self.sources} "
            f"({self.sources_with_record} with a record), kept texts {self.texts}, "
            f"passage index {'present' if self.passage_index else 'absent'}",
        ]
        if not self.reviews_known:
            lines.append(
                "  review rows were not read: held and rejected leftovers cannot be matched"
            )
        for category, entries in sorted(self.findings.items()):
            lines.append(f"  {category}: {len(entries)}")
            lines.extend(f"    - {e}" for e in entries[:20])
            if len(entries) > 20:
                lines.append(f"    ... and {len(entries) - 20} more")
        for q in self.quarantined:
            lines.append(f"  quarantined {q['path']}: {q['reason']}")
        lines.append(
            f"  apply would add records to {len(self.would_add_pages)} page(s) and "
            f"{len(self.would_add_sources)} original(s)"
        )
        return "\n".join(lines)


def provenance_source(text: str) -> str:
    prov = read_provenance(text) or {}
    name = str(prov.get("source") or "")
    return name if name and Path(name).name == name else ""


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _judge_page(
    rep: WorkspaceReport,
    slug: str,
    text: str,
    raw_names: set[str],
    referenced: dict[str, list[str]],
) -> None:
    source = provenance_source(text)
    if source:
        referenced.setdefault(source, []).append(slug)
        if source not in raw_names:
            rep.add("page_source_missing", f"{slug}: its original {source} is not kept")
    elif "<!-- anthill:" in text:
        rep.add("provenance_unreadable", f"{slug}: a provenance comment that does not parse")
    elif not PROVENANCE_RE.search(text):
        rep.add("page_without_provenance", slug)


def _page_findings(
    ws: Workspace, found: items.WorkspaceItems, rep: WorkspaceReport, raw_names: set[str]
) -> dict[str, list[str]]:
    """Judge every readable page and return which original each one names."""
    referenced: dict[str, list[str]] = {}
    damaged = {q.path for q in found.quarantined}
    for page in ws.pages():
        if f"wiki/{page.name}" in damaged:
            continue  # reported as quarantined, not judged
        text = _read_text(page)
        if text is not None:
            _judge_page(rep, page.stem, text, raw_names, referenced)
    return referenced


def _judge_original(
    rep: WorkspaceReport, name: str, review_status: Mapping[str, str] | None
) -> None:
    status = (review_status or {}).get(name)
    if status == "pending":
        rep.add("original_held_or_pending", f"{name}: its page waits for a review decision")
    elif status == "rejected":
        rep.add("original_rejected", f"{name}: its page was rejected")
    elif review_status is not None:
        rep.add(
            "original_no_page_no_review",
            f"{name}: no page and no review row, possibly a failed or timed-out upload",
        )
    else:
        rep.add("original_no_page", name)


def _duplicate_findings(rep: WorkspaceReport, raw_names: set[str]) -> None:
    by_hash: dict[str, list[str]] = {}
    for name in sorted(raw_names):
        m = _NAME_HASH.search(name)
        if m:
            by_hash.setdefault(m.group(1), []).append(name)
    for digest, names in sorted(by_hash.items()):
        if len(names) > 1:
            rep.add("duplicate_originals", f"{digest}: {', '.join(names)}")


def inventory(ws: Workspace, *, review_status: Mapping[str, str] | None = None) -> WorkspaceReport:
    """What a workspace holds, read only. ``review_status`` maps a stored original's name to the status of the
    review row that proposed its page ("pending", "approved", "rejected"); without it leftovers of held and
    rejected uploads cannot be told from any other original with no page."""
    space, ref = space_of_root(ws.root)
    rep = WorkspaceReport(root=str(ws.root), space=space, space_ref=ref)
    rep.reviews_known = review_status is not None
    found = items.list_items(ws)
    rep.pages = found.page_count
    rep.pages_with_record = len(found.pages)
    rep.sources = len(found.raw_names)
    rep.sources_with_record = len(found.sources)
    rep.quarantined = [{"path": q.path, "reason": q.reason} for q in found.quarantined]
    rep.passage_index = (ws.root / ".cache" / "doc_passages.sqlite3").is_file()
    raw_names = set(found.raw_names)
    text_names = (
        sorted(p.name[: -len(".txt")] for p in ws.text.glob("*.txt")) if ws.text.is_dir() else []
    )
    rep.texts = len(text_names)

    referenced = _page_findings(ws, found, rep, raw_names)
    for name in sorted(raw_names - set(referenced)):
        _judge_original(rep, name, review_status)
    for name in text_names:
        if name not in raw_names:
            rep.add("text_without_original", name)
    _duplicate_findings(rep, raw_names)
    rep.would_add_pages = sorted(found.unrecorded_pages)
    rep.would_add_sources = sorted(found.unrecorded_sources)
    return rep


# ── apply and undo ────────────────────────────────────────────────────────────


@dataclass
class ApplyResult:
    changed_pages: int = 0
    changed_sources: int = 0
    skipped: list[str] = field(default_factory=list)
    backup: str = ""
    manifest: str = ""

    @property
    def nothing_to_do(self) -> bool:
        return not self.changed_pages and not self.changed_sources


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backup_workspace(ws: Workspace, stamp: str) -> Path:
    """A tar.gz of ``wiki/`` and ``records/``: the only places ``apply_records`` writes. Originals and kept
    texts are never touched, so they are not copied. Written next to the workspace, read back to verify, and
    raises if it cannot be verified."""
    dest_dir = ws.root.parent / BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{ws.root.name}-{stamp}.tar.gz"
    count = 0
    with tarfile.open(dest, "w:gz") as tar:
        for sub in ("wiki", items.RECORDS_DIR):
            base = ws.root / sub
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    tar.add(p, arcname=str(p.relative_to(ws.root)))
                    count += 1
    with tarfile.open(dest, "r:gz") as tar:
        names = tar.getnames()
        if len(names) != count:
            raise OSError("the backup could not be verified")
        for member in tar.getmembers():
            fh = tar.extractfile(member)
            if fh is not None:
                while fh.read(1024 * 1024):
                    pass
    return dest


def _add_record_to_text(text: str, record: dict) -> str:
    """The page text with ``x-anthill-item`` added to its front matter. Everything that was there is kept byte
    for byte: the new key is appended at the end of the front matter, and the body is not touched. A page with
    no front matter gets a minimal block in front of its unchanged text."""
    addition = yaml.safe_dump(
        {FRONT_MATTER_KEY: record}, sort_keys=False, allow_unicode=True
    ).rstrip("\n")
    m = items._FRONT.match(text)
    if m is None:
        return f"---\ntype: Concept\n{addition}\n---\n{text}"
    return text[: m.end(2)] + "\n" + addition + text[m.end(2) :]


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` through a temporary file, keeping the permissions the file had."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, path.stat().st_mode & 0o7777)
    except OSError:
        pass  # a new file keeps the default permissions
    tmp.replace(path)


def _source_state(
    name: str, page_sources: Mapping[str, str], review_status: Mapping[str, str] | None
) -> str:
    """The review state an old original gets: approved when a page names it, else what its review row says."""
    if name in page_sources:
        return "checked"
    status = (review_status or {}).get(name)
    if status == "rejected":
        return "rejected"
    return "checked" if status == "approved" else "proposed"


def _page_sources_of(ws: Workspace) -> dict[str, str]:
    """Which page names each original in its provenance comment."""
    out: dict[str, str] = {}
    for page in ws.pages():
        text = _read_text(page)
        name = provenance_source(text) if text is not None else ""
        if name:
            out[name] = page.stem
    return out


def _apply_sources(
    ws: Workspace,
    names: list[str],
    state_of: Mapping[str, str],
    manifest: dict,
    result: ApplyResult,
) -> dict[str, str]:
    """Make a record for each original; returns name -> id for the ones made."""
    space, ref = space_of_root(ws.root)
    made: dict[str, str] = {}
    for name in names:
        try:
            rec = items.new_source_record(
                ws, ws.raw / name, origin="unknown", review_state=state_of[name]
            )
            rec.space, rec.space_ref = space, ref
            path = items.write_source_record(ws, rec)
        except (OSError, RecordError) as exc:
            result.skipped.append(f"{name}: {exc}")
            continue
        made[name] = rec.id
        manifest["sources"].append({"name": name, "sha256": _sha(path.read_bytes())})
        result.changed_sources += 1
    return made


def _links_for(source: str, source_ids: Mapping[str, str]) -> list[str]:
    if not source:
        return []
    return [source_ids[source]] if source in source_ids else [f"name:{source}"]


def _page_with_record(path: Path, slug: str, source_ids: Mapping[str, str]) -> tuple[bytes, bytes]:
    """(the page as it was, the page with its record added). Raises on a page that cannot be read or parsed."""
    space, ref = space_of_root(path.parent.parent)
    before = path.read_bytes()
    text = before.decode("utf-8")
    source = provenance_source(text)
    origin = items.origin_from_markdown(text)
    front, _rest = items.split_front_matter(text)
    fm = yaml.safe_load(front) if front else {}
    fm = fm if isinstance(fm, dict) else {}
    review = "approved" if fm.get("x-anthill-review") == "approved" else ""
    links = _links_for(source, source_ids)
    learned = (
        str(fm.get("timestamp") or "")
        or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    )
    rec = ItemRecord(
        kind="page",
        space=space,
        space_ref=ref,
        name=slug,
        origin=origin,
        review_state=items.initial_review_state(origin, review),
        learned=learned,
        content_hash=items.body_hash(text),
        derived_from=links,
    ).validate()
    return before, _add_record_to_text(text, rec.to_dict()).encode("utf-8")


def _apply_pages(
    ws: Workspace,
    slugs: list[str],
    source_ids: Mapping[str, str],
    manifest: dict,
    result: ApplyResult,
) -> None:
    for slug in slugs:
        path = ws.wiki / f"{slug}.md"
        if path.is_symlink():  # rewriting would replace the link with a file: leave it to its owner
            result.skipped.append(f"{slug}: a symbolic link, left as it is")
            continue
        try:
            before, after = _page_with_record(path, slug, source_ids)
            _atomic_write(path, after)
        except (OSError, UnicodeDecodeError, RecordError, yaml.YAMLError) as exc:
            result.skipped.append(f"{slug}: {exc.__class__.__name__}")
            continue
        manifest["pages"].append({"slug": slug, "before": _sha(before), "after": _sha(after)})
        result.changed_pages += 1


def apply_records(ws: Workspace, *, review_status: Mapping[str, str] | None = None) -> ApplyResult:
    """Give every page and every kept original without a record one. Backs up first; changes no page body;
    writes a manifest so ``undo_records`` can put everything back; does nothing (and writes no backup) when
    there is nothing to add. A damaged page or record is skipped and reported, never repaired."""
    result = ApplyResult()
    found = items.list_items(ws)
    todo_pages = sorted(found.unrecorded_pages)
    todo_sources = sorted(found.unrecorded_sources)
    if not todo_pages and not todo_sources:
        return result
    stamp = _stamp()
    backup = backup_workspace(ws, stamp)
    result.backup = str(backup)
    manifest: dict = {
        "created": items.now_iso(),
        "backup": f"{BACKUP_DIR}/{backup.name}",
        "pages": [],
        "sources": [],
    }
    page_sources = _page_sources_of(ws)
    state_of = {n: _source_state(n, page_sources, review_status) for n in todo_sources}
    source_ids = {r.name: r.id for r in found.sources}
    source_ids.update(_apply_sources(ws, todo_sources, state_of, manifest, result))
    _apply_pages(ws, todo_pages, source_ids, manifest, result)
    mpath = ws.root / items.RECORDS_DIR / f"{MANIFEST_PREFIX}{stamp}.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result.manifest = str(mpath)
    return result


@dataclass
class UndoResult:
    restored_pages: int = 0
    removed_sources: int = 0
    skipped: list[str] = field(default_factory=list)


def _undo_pages(ws: Workspace, entries: list[dict], backup: Path, out: UndoResult) -> None:
    with tarfile.open(backup, "r:gz") as tar:
        for entry in entries:
            path = ws.wiki / f"{entry['slug']}.md"
            if path.is_symlink():
                out.skipped.append(f"{entry['slug']}: a symbolic link, left as it is")
                continue
            try:
                if _sha(path.read_bytes()) != entry["after"]:
                    out.skipped.append(f"{entry['slug']}: changed since the records were added")
                    continue
                member = tar.extractfile(f"wiki/{entry['slug']}.md")
                original = member.read() if member else b""
                if _sha(original) != entry["before"]:
                    out.skipped.append(f"{entry['slug']}: the backup does not match")
                    continue
                _atomic_write(path, original)
                out.restored_pages += 1
            except (OSError, KeyError) as exc:
                out.skipped.append(f"{entry['slug']}: {exc.__class__.__name__}")


def _undo_sources(ws: Workspace, entries: list[dict], out: UndoResult) -> None:
    for entry in entries:
        path = items.source_record_path(ws, entry["name"])
        try:
            if path.is_file() and _sha(path.read_bytes()) == entry["sha256"]:
                path.unlink()
                out.removed_sources += 1
            elif path.is_file():
                out.skipped.append(f"{entry['name']}: its record changed since it was added")
        except OSError as exc:
            out.skipped.append(f"{entry['name']}: {exc.__class__.__name__}")


def undo_records(ws: Workspace, manifest_path: Path) -> UndoResult:
    """Put back what a manifest says ``apply_records`` changed. A page is restored from the backup only if it
    is still exactly as apply left it; a page changed since is left alone and reported."""
    out = UndoResult()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup = ws.root.parent / manifest["backup"]
    if not backup.is_file():
        raise OSError(f"the backup {backup} is gone")
    _undo_pages(ws, manifest["pages"], backup, out)
    _undo_sources(ws, manifest["sources"], out)
    sidecars = ws.root / items.RECORDS_DIR / items.SOURCES_DIR
    if sidecars.is_dir() and not any(sidecars.iterdir()):
        sidecars.rmdir()
    manifest_path.rename(manifest_path.with_name(manifest_path.name + ".undone"))
    base = ws.root / items.RECORDS_DIR
    if base.is_dir() and not any(base.iterdir()):
        base.rmdir()
    return out


def render_json(reports: list[WorkspaceReport]) -> str:
    return json.dumps([r.to_dict() for r in reports], indent=2)
