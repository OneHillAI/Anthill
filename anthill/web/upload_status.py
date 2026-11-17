"""What became of each document a person added to a wiki, and why one could not be read.

An upload used to leave only a one-time banner. This keeps a short record per upload (``UploadRecord``)
that the wiki page lists, and turns a failure into a sentence that says what happened and what to do next
(founder, 2026-10-01: a technical reason with no action is not help). Records are best-effort: a problem
writing one never stops the upload itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from ..inference.base import BackendError
from ..lifecycle.version import read_provenance
from ..multimodal.reader import (
    PDF_HARD_MAX_PAGES,
    PDF_HARD_MAX_SOURCE_BYTES,
    PDF_HARD_PROCESSING_SECONDS,
)
from ..wiki.ingest import IngestTimedOut
from .db import UploadRecord

logger = logging.getLogger("anthill.web.upload_status")

RECENT_UPLOADS_SHOWN = 10
# The longest a document is allowed to take (the PDF budget) plus the two minutes the wiki page's own
# progress bar keeps waiting. A record still "reading" after this was cut off, not slow.
READING_STALE_AFTER = timedelta(seconds=PDF_HARD_PROCESSING_SECONDS + 120)
VERBATIM_MODEL = "verbatim"  # the tag a note filed as written carries (#181)

SETTINGS_MODEL = "/personalize#model"


@dataclass(frozen=True)
class Problem:
    text: str
    link_text: str = ""
    href: str = ""


UPLOAD_PROBLEMS: dict[str, Problem] = {
    "ingest": Problem(
        "Anthill couldn't read that document, so nothing was added. Try uploading it again. If it "
        "keeps failing, save it as a PDF or a plain text file and upload that instead."
    ),
    "aiunreachable": Problem(
        "Couldn't reach your AI to read that document, so nothing was added. Check that your model "
        "is installed and running, then upload the file again.",
        "Check your model setup",
        SETTINGS_MODEL,
    ),
    "slow": Problem(
        "Your AI took too long to read that document, so nothing was added. It may be busy or still "
        "loading. Try again in a few minutes.",
        "Check your model setup",
        SETTINGS_MODEL,
    ),
    "scan": Problem(
        "That PDF is made of page images (a scan) and your current AI reads text only. Add an "
        "image-reading model in Settings > Model, or upload a text version.",
        "Open Settings > Model",
        SETTINGS_MODEL,
    ),
    "scanremote": Problem(
        "That PDF is made of page images (a scan), and reading those needs an image-reading model "
        "running on this computer. Yours runs somewhere else. Add one on this computer in Settings > "
        "Model, or upload a text version.",
        "Open Settings > Model",
        SETTINGS_MODEL,
    ),
    "imagetext": Problem(
        "That picture can't be read because your current AI reads text only. Add an image-reading "
        "model in Settings > Model, or upload the text instead.",
        "Open Settings > Model",
        SETTINGS_MODEL,
    ),
    "scanempty": Problem(
        "Anthill couldn't find any pages it can read in that PDF. It may be empty or damaged. Save "
        "it again from the program that made it, or upload a text version."
    ),
    "pdfconfirm": Problem(
        'This PDF is longer or bigger than Anthill reads without asking. Tick the box "Confirm '
        'larger PDF processing" under the file box, choose the file again and click Upload.'
    ),
    "pdflimit": Problem(
        f"This PDF is more than Anthill can read in one go (the limits are {PDF_HARD_MAX_PAGES} "
        f"pages, {PDF_HARD_MAX_SOURCE_BYTES // (1024 * 1024)} MB and "
        f"{PDF_HARD_PROCESSING_SECONDS // 60} minutes of reading), so nothing was added. Split it "
        "into smaller PDFs, for example by chapter, and upload each one."
    ),
    "pdfretry": Problem(
        "Anthill couldn't finish reading that PDF this time. Nothing is wrong with the file. Wait "
        "a minute, then try again. If it keeps happening, check that your AI is running.",
        "Check your model setup",
        SETTINGS_MODEL,
    ),
}

_STATUS_LABELS = {
    "reading": "Reading",
    "added": "Added",
    "review": "Waiting for your review",
    "held": "Held for your decision",
    "waiting_model": "Waiting for the model to finish downloading",
    "timed_out": "Timed out",
    "failed": "Couldn't read",
    "interrupted": "Interrupted - try again",
}
_STATUS_MEANINGS = {
    "reading": "Your AI is reading it now. A long document can take a few minutes. Reload this "
    "page to see the result.",
    "added": "It is now a page in this wiki.",
    "review": "Added as a draft because the automatic check flagged it. Open the review queue to "
    "approve it or change it.",
    "held": "This wiki already has the same file or a page with that title, so nothing changed. "
    "Open the review queue to choose what happens.",
    "waiting_model": "Your AI is still downloading. The file is saved and will be added by itself "
    "when the download finishes, so you do not need to upload it again.",
    "timed_out": 'Your AI did not finish in time, so nothing was added. Use "Try again with a '
    'longer limit" above; you do not need to choose the file again.',
    "interrupted": "Anthill stopped waiting for this one, so it may not have been added. Check "
    "the Pages list, then upload the file again.",
}
_FROM_PROPOSAL = {"added": "added", "queued": "review", "held": "held"}


def classify_failure(exc: BaseException, health_problem: str, filename: str = "") -> str:
    """The ``UPLOAD_PROBLEMS`` code for a failed read.

    The image-reading cases come from the reader's own messages (``wiki.ingest._vision_backend_config``;
    a test keeps them in step). "Couldn't reach your AI" is kept for a real connection failure only: a
    network error anywhere in the exception's chain, or a model backend that reports itself unhealthy.
    ``health_problem`` is that backend's own health report ("" when it has none or is fine).
    """
    if isinstance(exc, IngestTimedOut):
        return "slow"
    if isinstance(exc, BackendError):
        message = str(exc)
        if "no extractable page images" in message:
            return "scanempty"
        if "loopback interface" in message:
            return "scanremote"
        if "vision model" in message:
            return "scan" if filename.lower().endswith(".pdf") else "imagetext"
    link: BaseException | None = exc
    seen: set[int] = set()
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        if isinstance(link, httpx.TransportError):
            return "aiunreachable"
        link = link.__cause__ or link.__context__
    if isinstance(exc, BackendError) and health_problem:
        return "aiunreachable"
    return "ingest"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: str, size: int) -> str:
    return (value or "")[:size]


def _save(db, rec: UploadRecord | None, **fields) -> None:
    if rec is None:
        return
    try:
        for name, value in fields.items():
            setattr(rec, name, value)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("could not update upload record", exc_info=True)


def begin_upload(
    db,
    *,
    org_id: int,
    user_id: int,
    scope: str,
    team_id: int | None,
    filename: str,
    status: str = "reading",
    queued_upload_id: int | None = None,
) -> UploadRecord | None:
    try:
        rec = UploadRecord(
            org_id=org_id,
            user_id=user_id,
            scope=scope,
            team_id=team_id,
            filename=_clip(filename, 255),
            status=status,
            queued_upload_id=queued_upload_id,
        )
        db.add(rec)
        db.commit()
        return rec
    except Exception:
        db.rollback()
        logger.warning("could not record an upload", exc_info=True)
        return None


def record_for_queued(db, queued_upload_id: int, user_id: int) -> UploadRecord | None:
    return (
        db.query(UploadRecord)
        .filter(UploadRecord.queued_upload_id == queued_upload_id, UploadRecord.user_id == user_id)
        .first()
    )


def restart_upload(db, rec: UploadRecord | None) -> None:
    _save(db, rec, status="reading", detail="", created_at=_now(), finished_at=None)


def finish_upload(db, rec, proposal_status: str, slug: str, title: str, page_md: str) -> None:
    """The page was filed: ``proposal_status`` is ``propose_upload``'s added / queued / held."""
    provenance = read_provenance(page_md) or {}
    _save(
        db,
        rec,
        status=_FROM_PROPOSAL.get(proposal_status, "added"),
        detail="",
        slug=_clip(slug, 120),
        title=_clip(title, 300),
        model=_clip(provenance.get("model", ""), 120),
        finished_at=_now(),
    )


def fail_upload(db, rec, code: str) -> None:
    _save(db, rec, status="failed", detail=_clip(code, 40), finished_at=_now())


def time_out_upload(db, rec, queued_upload_id: int | None) -> None:
    _save(db, rec, status="timed_out", queued_upload_id=queued_upload_id, finished_at=_now())


def forget_timed_out(db, queued_upload_ids: list[int]) -> None:
    """A later upload of the same file went through, so its earlier "Timed out" line is out of date.
    The caller commits, together with settling the retry rows, so the two never get out of step."""
    if not queued_upload_ids:
        return
    db.query(UploadRecord).filter(
        UploadRecord.queued_upload_id.in_(queued_upload_ids), UploadRecord.status == "timed_out"
    ).delete(synchronize_session=False)


def how_it_was_read(
    db, *, org_id: int, user_id: int, scope: str, team_id: int | None, slug: str
) -> str:
    """One sentence on what read the document that became page ``slug``, from the person's latest upload
    record for it, or "" when there is no such record or it names no model. It is the model the page itself
    is tagged with (see ``finish_upload``), not a guess from the account's settings."""
    row = (
        db.query(UploadRecord)
        .filter(
            UploadRecord.org_id == org_id,
            UploadRecord.user_id == user_id,
            UploadRecord.scope == scope,
            UploadRecord.team_id == team_id,
            UploadRecord.slug == slug,
            UploadRecord.status.in_(("added", "review", "held")),
        )
        .order_by(UploadRecord.id.desc())
        .first()
    )
    if row is None or not row.model:
        return ""
    if row.model == VERBATIM_MODEL:
        return "Filed exactly as written: no AI rewrote it."
    return f"Read by {row.model}, which wrote the page from your document."


def _review_url(scope: str, team_id: int | None) -> str:
    if scope == "org":
        return "/wiki/review"
    if scope == "team" and team_id:
        return f"/teams/{team_id}"
    return "/wiki/review/personal"


def _model_text(status: str, model: str) -> str:
    if status not in ("added", "review", "held") or not model:
        return ""
    return "filed as written" if model == VERBATIM_MODEL else f"read by {model}"


def recent_uploads(db, *, org_id: int, user_id: int, scope: str, team_id: int | None) -> list[dict]:
    """The person's own latest uploads to this wiki, newest first. Nobody else's are listed, an org
    admin's included: a file name can say more than the page it became."""
    rows = (
        db.query(UploadRecord)
        .filter(
            UploadRecord.org_id == org_id,
            UploadRecord.user_id == user_id,
            UploadRecord.scope == scope,
            UploadRecord.team_id == team_id,
        )
        .order_by(UploadRecord.id.desc())
        .limit(RECENT_UPLOADS_SHOWN)
        .all()
    )
    now = _now()
    out = []
    for r in rows:
        status = r.status
        started = r.created_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if status == "reading" and started is not None and now - started > READING_STALE_AFTER:
            status = "interrupted"
        problem = UPLOAD_PROBLEMS.get(r.detail, UPLOAD_PROBLEMS["ingest"])
        item = {
            "filename": r.filename,
            "label": _STATUS_LABELS.get(status, status),
            "meaning": problem.text if status == "failed" else _STATUS_MEANINGS.get(status, ""),
            "model": _model_text(status, r.model),
            "created_at": r.created_at,
            "href": "",
            "href_text": "",
        }
        if status == "failed" and problem.href:
            item["href"], item["href_text"] = problem.href, problem.link_text
        elif status == "added" and r.slug:
            team = f"&team_id={r.team_id}" if r.team_id else ""
            item["href"] = f"/wiki/page/{quote(r.slug)}?scope={r.scope}{team}"
            item["href_text"] = "View the page"
        elif status in ("review", "held"):
            item["href"] = _review_url(r.scope, r.team_id)
            item["href_text"] = "Open the review queue"
        elif status == "timed_out":
            item["href"], item["href_text"] = "#wiki-upload-retries", "Go to the retry button"
        out.append(item)
    return out
