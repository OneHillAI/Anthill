"""Remove a document or a page, with a way back for 30 days. Spec: docs/specs/remove-an-item.md; issue #278.

Removing moves the item's files out of the folders every reader scans (``wiki/``, ``raw/``, ``text/``,
``records/``) into ``<workspace>/.trash/<id>/``, keeping their layout, and writes a small manifest beside them. Nothing
that reads those folders can see the item, so answers stop using it at once and no reader has to remember to check.
``restore`` moves the files back. ``delete`` removes them for good, with what was derived from them. An item removed
more than ``RETENTION_DAYS`` ago is deleted by ``expire`` (at start-up, and when the list of removed items is opened).

Nothing here calls a model."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import items
from .workspace import Workspace, workspace_at

log = logging.getLogger(__name__)

TRASH_DIR = ".trash"
MANIFEST = "removal.json"
RETENTION_DAYS = 30


class RemovalError(ValueError):
    """The removal cannot be done, with a reason a person can read."""


@dataclass
class Removed:
    id: str
    kind: str  # "page" or "original"
    name: str  # a page's slug, or an original's stored name
    removed_at: str
    files: list[str] = field(default_factory=list)  # paths relative to the workspace, as they were
    pages: list[str] = field(default_factory=list)  # slugs of the pages that went with an original
    deleting: bool = False  # a delete that was started; finished at the next start

    def days_left(self, now: datetime | None = None) -> int:
        end = datetime.fromisoformat(self.removed_at) + timedelta(days=RETENTION_DAYS)
        left = (end - (now or datetime.now(timezone.utc))).total_seconds()
        return max(math.ceil(left / 86400), 0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "removed_at": self.removed_at,
            "files": self.files,
            "pages": self.pages,
            "deleting": self.deleting,
        }


@dataclass
class Plan:
    """What removing an original would do to pages: those that go with it and those that stay."""

    goes: list[str] = field(default_factory=list)
    stays: list[str] = field(default_factory=list)


_ID = re.compile(r"^[0-9a-f]{12}$")


def _bundle(ws: Workspace, rid: str) -> Path:
    if not _ID.match(rid):
        raise RemovalError("that is not a removed item")
    return ws.root / TRASH_DIR / rid


def _write_manifest(ws: Workspace, rec: Removed) -> None:
    path = _bundle(ws, rec.id) / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_manifest(path: Path) -> Removed | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Removed(
            id=str(data["id"]),
            kind=str(data["kind"]),
            name=str(data["name"]),
            removed_at=str(data["removed_at"]),
            files=[str(f) for f in data.get("files", [])],
            pages=[str(p) for p in data.get("pages", [])],
            deleting=bool(data.get("deleting", False)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_removed(ws: Workspace) -> list[Removed]:
    """What has been removed from this workspace and not yet deleted, newest first."""
    base = ws.root / TRASH_DIR
    if not base.is_dir():
        return []
    out = [r for d in base.iterdir() if (r := _read_manifest(d / MANIFEST)) is not None]
    return sorted(out, key=lambda r: r.removed_at, reverse=True)


def _move(ws: Workspace, rec: Removed, rels: list[str]) -> None:
    """Move each file into the bundle, keeping its place. If one cannot be moved the ones already moved are put back."""
    done: list[str] = []
    try:
        for rel in rels:
            src, dst = ws.root / rel, _bundle(ws, rec.id) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            done.append(rel)
    except OSError as exc:
        for rel in done:
            os.replace(_bundle(ws, rec.id) / rel, ws.root / rel)
        shutil.rmtree(_bundle(ws, rec.id), ignore_errors=True)
        raise RemovalError(f"could not move {rel}: {exc.__class__.__name__}") from exc


def plan_original(ws: Workspace, name: str) -> Plan:
    """The pages made from this original: a page goes with it only when a model wrote it, no person has checked
    it, and it has a record to say so. Every other page made from it stays."""
    plan = Plan()
    for page in ws.pages():
        try:
            text = page.read_text(encoding="utf-8")
            if items.source_name_of(text) != name:
                continue
            rec = items.read_page_record(text)
        except (OSError, UnicodeDecodeError, items.RecordError):
            plan.stays.append(page.stem)
            continue
        goes = (
            rec is not None
            and rec.origin == "model-written"
            and rec.review_state == "unchecked"
            and rec.lifecycle == "active"
        )
        (plan.goes if goes else plan.stays).append(page.stem)
    return plan


def _new(kind: str, name: str) -> Removed:
    return Removed(
        id=uuid.uuid4().hex[:12],
        kind=kind,
        name=name,
        removed_at=datetime.now(timezone.utc).isoformat(),
    )


def _finish_removal(ws: Workspace, rec: Removed, rels: list[str]) -> Removed:
    rec.files = rels
    _move(ws, rec, rels)
    _write_manifest(ws, rec)
    ws.rebuild_index()
    _forget_in_search(ws, [Path(r).stem for r in rels if r.startswith("wiki/")])
    ws.append_log("remove", rec.name)
    return rec


def _forget_in_search(ws: Workspace, slugs: list[str]) -> None:
    """Take removed pages out of the search index when one is in use, so a removed page is not left in it."""
    from ..search import meili

    if slugs and meili.enabled():
        meili.delete_pages(slugs, index=ws.meili_index)


def remove_page(ws: Workspace, slug: str) -> Removed:
    """Remove one page. Its original, if it has one, stays."""
    if Path(slug).name != slug or not (ws.wiki / f"{slug}.md").is_file():
        raise RemovalError(f"there is no page {slug!r}")
    return _finish_removal(ws, _new("page", slug), [f"wiki/{slug}.md"])


def remove_original(ws: Workspace, name: str) -> Removed:
    """Remove an original with its kept text and its record, and the pages ``plan_original`` says go with it."""
    if Path(name).name != name or name.startswith(".") or not (ws.raw / name).is_file():
        raise RemovalError(f"there is no original {name!r}")
    plan = plan_original(ws, name)
    rels = [f"raw/{name}"]
    for extra in (f"text/{name}.txt", f"{items.RECORDS_DIR}/{items.SOURCES_DIR}/{name}.json"):
        if (ws.root / extra).is_file():
            rels.append(extra)
    rels += [f"wiki/{slug}.md" for slug in plan.goes]
    rec = _new("original", name)
    rec.pages = list(plan.goes)
    _finish_removal(ws, rec, rels)
    _drop_passages(ws, name)
    return rec


def _drop_passages(ws: Workspace, name: str) -> None:
    from .doc_index import DocIndex

    DocIndex(ws.root / ".cache", ws.text).drop(name)


def restore(ws: Workspace, rid: str) -> Removed:
    """Put a removed item back. Refuses, and says why, when something has taken its place since."""
    rec = _read_manifest(_bundle(ws, rid) / MANIFEST)
    if rec is None or rec.deleting:
        raise RemovalError("that item is no longer there to restore")
    taken = [rel for rel in rec.files if (ws.root / rel).exists()]
    if taken:
        raise RemovalError(f"cannot restore: {', '.join(taken)} already exists")
    for rel in rec.files:
        dst = ws.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(_bundle(ws, rid) / rel, dst)
    shutil.rmtree(_bundle(ws, rid), ignore_errors=True)
    _reindex_restored(ws, rec)
    ws.rebuild_index()
    ws.append_log("restore", rec.name)
    return rec


def _reindex_restored(ws: Workspace, rec: Removed) -> None:
    from .doc_index import index_text_file
    from .page_index import warm_page_soon

    for rel in rec.files:
        path = ws.root / rel
        if rel.startswith("text/"):
            index_text_file(ws.root, path)
        elif rel.startswith("wiki/"):
            warm_page_soon(ws.root, path)


def delete(ws: Workspace, rid: str) -> Removed:
    """Delete a removed item for good, with what was derived from it. A delete that is interrupted is finished by
    ``expire``: the manifest says it was started."""
    rec = _read_manifest(_bundle(ws, rid) / MANIFEST)
    if rec is None:
        raise RemovalError("that item is no longer there")
    if not rec.deleting:
        rec.deleting = True
        _write_manifest(ws, rec)
    from ..cache.page_vectors import PageVectorIndex

    pages = [rel for rel in rec.files if rel.startswith("wiki/")]
    PageVectorIndex(ws.root / ".cache").forget(pages)
    if rec.kind == "original":
        _drop_passages(ws, rec.name)
    shutil.rmtree(_bundle(ws, rid), ignore_errors=True)
    ws.append_log("delete", rec.name)
    return rec


def expire(ws: Workspace, now: datetime | None = None) -> int:
    """Delete what was removed more than ``RETENTION_DAYS`` ago, and finish any delete that was started."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for rec in list_removed(ws):
        if rec.deleting or now - datetime.fromisoformat(rec.removed_at) >= timedelta(
            days=RETENTION_DAYS
        ):
            try:
                delete(ws, rec.id)
                n += 1
            except (RemovalError, OSError) as exc:
                log.warning("could not delete the removed item %s: %s", rec.name, exc)
    return n


def expire_all(now: datetime | None = None) -> int:
    """``expire`` for every wiki on this install. Never raises."""
    from .page_index import workspace_roots

    n = 0
    try:
        for root in workspace_roots():
            if (root / TRASH_DIR).is_dir():
                n += expire(workspace_at(root), now)
    except (OSError, ValueError) as exc:
        log.warning("could not delete old removed items: %s", exc)
    return n
