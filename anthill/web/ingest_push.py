"""Inbound push event sources (webhooks + IMAP) that feed the workspace ``inbox/``.

This module is the pure, testable core shared by the FastAPI webhook route and the
IMAP IDLE worker: secret/signature verification, turning a Slack / generic / email
payload into a (title, body) document, and writing it safely into ``inbox/``.

Security model: an inbound event becomes a **data file** in ``inbox/`` and is
ingested through the normal agent review gate (``_event_tick`` -> ``propose_wiki_write``).
It is never executed, and the body is never treated as instructions to the agent.
"""

from __future__ import annotations

import email
import hashlib
import hmac
import json
import re
from email import policy
from pathlib import Path

# Slug charset excludes '.' so a payload can't smuggle '..' into a filename; the
# file extension is appended separately by inbox_filename().
_UNSAFE = re.compile(r"[^a-z0-9_-]+")


def _slug(s: str, *, maxlen: int = 40) -> str:
    out = _UNSAFE.sub("-", (s or "").lower()).strip("-")
    return (out[:maxlen].strip("-")) or "event"


def inbox_filename(source: str, title: str, *, stamp: str, ext: str = ".md") -> str:
    """A safe, sortable inbox filename: ``<stamp>-<source>-<title><ext>``.

    Fully sanitized: no path separators survive, so it can't escape ``inbox/``.
    """
    name = f"{_slug(stamp, maxlen=24)}-{_slug(source, maxlen=16)}-{_slug(title)}"
    return name[:120] + ext


def write_event(inbox_dir, source: str, title: str, body: str, *, stamp: str) -> Path:
    """Write one inbound event into ``inbox/`` as a markdown file. Returns its path.

    Creates the directory if needed and guards against path traversal even though
    the filename is already sanitized (belt and suspenders).
    """
    d = Path(inbox_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = (d / inbox_filename(source, title, stamp=stamp)).resolve()
    if d.resolve() != p.parent:
        raise ValueError("unsafe inbox path")
    doc = f"# {title}\n\n{body}".strip() + "\n" if title else (body.strip() + "\n")
    p.write_text(doc, encoding="utf-8")
    return p


# ── secret / signature verification ───────────────────────────────────────────


def verify_token(expected: str, provided: str) -> bool:
    """Constant-time equality for the generic-webhook shared token."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(expected), str(provided))


def slack_signature(signing_secret: str, timestamp: str, body: bytes) -> str:
    """Slack's v0 request signature for a body. (https://api.slack.com/authentication/verifying-requests-from-slack)"""
    base = b"v0:" + str(timestamp).encode() + b":" + body
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return "v0=" + digest


def verify_slack(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: int | None = None,
    max_skew_s: int = 300,
) -> bool:
    """True iff the Slack signature is valid and the request is recent (replay guard)."""
    if not (signing_secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if now is not None and abs(now - ts) > max_skew_s:
        return False
    return hmac.compare_digest(slack_signature(signing_secret, timestamp, body), signature)


# ── payload -> (title, body) ───────────────────────────────────────────────────


def slack_event_text(payload: dict) -> tuple[str, str] | None:
    """(title, body) for a user Slack message event, or ``None`` to ignore.

    Ignores non-message events, bot messages, and edits/joins/leaves (subtypes) so
    the inbox only sees real human messages.
    """
    if not isinstance(payload, dict):
        return None
    ev = payload.get("event") or {}
    if ev.get("type") != "message" or ev.get("bot_id") or ev.get("subtype"):
        return None
    text = (ev.get("text") or "").strip()
    if not text:
        return None
    chan = ev.get("channel", "")
    return (f"Slack message in {chan}" if chan else "Slack message", text)


def generic_event_text(raw: bytes, content_type: str = "") -> tuple[str, str]:
    """(title, body) for a generic webhook. JSON bodies use title/subject/event +
    text/body/message keys when present; otherwise the raw payload is the body."""
    body = raw.decode("utf-8", "replace").strip()
    title = "Webhook event"
    if "json" in (content_type or "").lower():
        try:
            data = json.loads(body or "{}")
        except ValueError:
            data = None
        if isinstance(data, dict):
            title = str(data.get("title") or data.get("subject") or data.get("event") or title)[
                :120
            ]
            text = data.get("text") or data.get("body") or data.get("message")
            body = str(text) if text else json.dumps(data, indent=2, ensure_ascii=False)
    return title, (body or "(empty)")


# ── email ──────────────────────────────────────────────────────────────────────


def _email_body(msg) -> str:
    """The plaintext body of an email, preferring text/plain over stripped HTML."""
    if msg.is_multipart():
        plain = None
        html = None
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            if ctype == "text/plain" and plain is None:
                plain = part.get_content()
            elif ctype == "text/html" and html is None:
                html = part.get_content()
        if plain:
            return plain.strip()
        if html:
            return re.sub(r"<[^>]+>", " ", html).strip()
        return ""
    try:
        return (msg.get_content() or "").strip()
    except Exception:
        return ""


def parse_email_bytes(raw: bytes) -> dict:
    """Parse a raw RFC822 message into ``{subject, from, body}`` (plaintext)."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    return {
        "subject": (str(msg.get("subject", "")).strip() or "(no subject)"),
        "from": str(msg.get("from", "")).strip(),
        "body": _email_body(msg),
    }


def email_to_doc(parsed: dict) -> tuple[str, str]:
    """(title, body) for an email dict from :func:`parse_email_bytes`."""
    title = parsed.get("subject") or "Email"
    header = []
    if parsed.get("from"):
        header.append(f"From: {parsed['from']}")
    if parsed.get("subject"):
        header.append(f"Subject: {parsed['subject']}")
    body = ("\n".join(header) + "\n\n" + (parsed.get("body") or "")).strip()
    return title, body
