"""A proposal waiting for review is kept in a file as well as in the database. Spec: docs/specs/database-is-an-index.md.

A change proposed to a wiki (``WikiReview``, status pending) is written as ``proposals/<uid>.json`` in the workspace it is
for, when the database commits it, and the file is removed when the proposal is decided or deleted. Nothing that is
knowledge lives only in the database: ``reconcile`` puts back the pending proposals of a database that has lost them,
from the files, at start-up.

The file holds what the row holds apart from its own ids. It is written whole (a temporary file, then a rename)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

log = logging.getLogger(__name__)

FOLDER = "proposals"
_UID = re.compile(r"^[0-9a-f]{32}$")
_FIELDS = (
    "org_id",
    "proposed_by",
    "source_conversation_id",
    "source_message_id",
    "provenance_hash",
    "slug",
    "kind",
    "content",
    "diff_summary",
    "target_scope",
    "team_id",
    "outline",
    "flags",
    "status",
)
_OPS_KEY = "proposal_file_ops"
_RESTORING = (
    "proposal_files_restoring"  # a row put back from its file is not written to its file again
)


def _workspace(row):
    from ..wiki.workspace import workspace_for

    return workspace_for(
        row["target_scope"],
        team_id=row["team_id"],
        user_id=row["proposed_by"],
        org_id=row["org_id"],
    )


def _snapshot(target) -> dict:
    snap = {name: getattr(target, name) for name in _FIELDS}
    snap["uid"] = target.uid
    snap["created_at"] = (target.created_at or datetime.now(timezone.utc)).isoformat()
    return snap


def file_path(root: Path, uid: str) -> Path:
    if not _UID.match(uid):
        raise ValueError("not a proposal id")
    return root / FOLDER / f"{uid}.json"


def _write(snap: dict) -> None:
    from ..wiki.page_store import atomic_write

    ws = _workspace(snap)
    if not ws.root.is_dir():  # a wiki that is not there is not made for a proposal
        return
    atomic_write(file_path(ws.root, snap["uid"]), json.dumps(snap, indent=2) + "\n")


def _remove(snap: dict) -> None:
    ws = _workspace(snap)
    file_path(ws.root, snap["uid"]).unlink(missing_ok=True)


def _note(target, op: str) -> None:
    session = object_session(target)
    if session is None or not target.uid or session.info.get(_RESTORING):
        return
    snap = _snapshot(target)
    if op == "write" and snap["status"] != "pending":
        op = "remove"  # decided: it is no longer waiting
    session.info.setdefault(_OPS_KEY, {})[target.uid] = (op, snap)


def install(model) -> None:
    """Keep a file for every pending proposal of ``model`` (``WikiReview``). Called once, from ``db``."""

    @event.listens_for(model, "before_insert")
    def _give_uid(mapper, connection, target):
        if not target.uid:
            import uuid

            target.uid = uuid.uuid4().hex

    @event.listens_for(model, "after_insert")
    def _inserted(mapper, connection, target):
        _note(target, "write")

    @event.listens_for(model, "after_update")
    def _updated(mapper, connection, target):
        _note(target, "write")

    @event.listens_for(model, "after_delete")
    def _deleted(mapper, connection, target):
        _note(target, "remove")


@event.listens_for(Session, "after_commit")
def _apply(session) -> None:
    ops = session.info.pop(_OPS_KEY, None) or {}
    for op, snap in ops.values():
        try:
            (_write if op == "write" else _remove)(snap)
        except Exception as exc:  # noqa: BLE001 - a file that cannot be kept must not undo a committed proposal
            log.warning("could not keep the proposal file for %s: %s", snap.get("slug"), exc)


@event.listens_for(Session, "after_rollback")
def _forget(session) -> None:
    session.info.pop(_OPS_KEY, None)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not _UID.match(str(data.get("uid", ""))):
        return None
    if data.get("status") != "pending" or any(name not in data for name in _FIELDS):
        return None
    return data


def reconcile(db) -> int:
    """Put back, from the files, the pending proposals the database no longer has. A proposal whose organisation or team
    is gone is left in its file; one whose proposer is gone is attributed to the owner of a personal wiki and has none
    otherwise. Returns how many were put back. Never raises."""
    from ..wiki.page_index import workspace_roots
    from .db import Organization, Team, User, WikiReview

    put_back = 0
    db.info[_RESTORING] = True
    try:
        for root in workspace_roots():
            folder = root / FOLDER
            for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
                data = _read(path)
                if (
                    data is None
                    or db.query(WikiReview).filter(WikiReview.uid == data["uid"]).first()
                ):
                    continue
                if db.get(Organization, data["org_id"]) is None:
                    continue
                if data["team_id"] is not None and db.get(Team, data["team_id"]) is None:
                    continue
                proposer = data["proposed_by"]
                if proposer is not None and db.get(User, proposer) is None:
                    owner = re.fullmatch(r"user-(\d+)", root.name)
                    proposer = (
                        int(owner.group(1))
                        if owner and data["target_scope"] == "personal"
                        else None
                    )
                    if proposer is not None and db.get(User, proposer) is None:
                        proposer = None
                fields = {name: data[name] for name in _FIELDS} | {"proposed_by": proposer}
                db.add(
                    WikiReview(**fields, uid=data["uid"], created_at=_when(data.get("created_at")))
                )
                put_back += 1
        db.commit()
    except Exception as exc:  # noqa: BLE001 - start-up goes on without them; the files are still there
        db.rollback()
        log.warning("could not put back the pending proposals: %s", exc)
        return 0
    finally:
        db.info.pop(_RESTORING, None)
    return put_back


def _when(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now(timezone.utc)
