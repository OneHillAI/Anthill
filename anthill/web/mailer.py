"""Outbound transactional email (best-effort).

SMTP is configured via the environment:
  ANTHILL_SMTP_HOST, ANTHILL_SMTP_PORT (587), ANTHILL_SMTP_USER, ANTHILL_SMTP_PASSWORD,
  ANTHILL_SMTP_FROM, ANTHILL_SMTP_TLS (1).

This works with any SMTP relay, including Proton Mail's SMTP submission
(host smtp.protonmail.ch, port 587, STARTTLS; USER = your Proton address,
PASSWORD = an SMTP token from Proton settings, not your login password - paid plan + custom domain).

If SMTP isn't configured or a send fails, the send functions return False and the caller falls back
(surface the link in the browser / console, or contact-admin). The functions never raise. Standard
library only - no new dependency.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from . import email_templates as tpl

log = logging.getLogger("anthill.mailer")


def smtp_configured() -> bool:
    return bool(os.environ.get("ANTHILL_SMTP_HOST") and os.environ.get("ANTHILL_SMTP_FROM"))


def _deliver(msg: EmailMessage, to: str) -> bool:
    """Set the envelope, connect, and send. Returns False if SMTP is unconfigured or the send fails."""
    host = os.environ.get("ANTHILL_SMTP_HOST")
    sender = os.environ.get("ANTHILL_SMTP_FROM")
    if not (host and sender and to):
        return False
    port = int(os.environ.get("ANTHILL_SMTP_PORT", "587"))
    user = os.environ.get("ANTHILL_SMTP_USER")
    password = os.environ.get("ANTHILL_SMTP_PASSWORD")
    use_tls = os.environ.get("ANTHILL_SMTP_TLS", "1") not in ("0", "false", "no")
    msg["From"], msg["To"] = sender, to
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            if use_tls:
                s.starttls(context=ssl.create_default_context())
            if user and password:
                s.login(user, password)
            s.send_message(msg)
        return True
    except Exception as e:
        # Best-effort: the caller falls back (in-browser link / contact-admin), so never raise. But
        # log it - a silent swallow here once made a working config look unconfigured for hours (the
        # real cause was TLS cert verification failing in the frozen app; see desktop._ensure_tls_certs).
        log.warning("SMTP send to %s via %s:%s failed: %s: %s", to, host, port, type(e).__name__, e)
        return False


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns False if SMTP isn't configured or the send fails."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg.set_content(body)
    return _deliver(msg, to)


def send_html(to: str, subject: str, html: str, text: str) -> bool:
    """Send a multipart/alternative email (plain-text + HTML). False if unconfigured or the send fails."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return _deliver(msg, to)


def send_welcome_email(to: str, *, name: str = "", org_name: str = "", url: str = "") -> bool:
    """Welcome a newly-signed-up user. True if the email was sent."""
    subject, html, text = tpl.welcome(name=name, org_name=org_name, url=url)
    return send_html(to, subject, html, text)


def send_verify_email(to: str, verify_url: str, *, name: str = "", org_name: str = "") -> bool:
    """Email an account-activation (email-confirmation) link. True if the email was sent."""
    subject, html, text = tpl.verify_email(verify_url=verify_url, name=name, org_name=org_name)
    return send_html(to, subject, html, text)


def send_reset_email(to: str, reset_url: str, *, org_name: str = "", ttl_minutes: int = 60) -> bool:
    """Email a password-reset link. True if the email was sent."""
    subject, html, text = tpl.password_reset(
        reset_url=reset_url, org_name=org_name, ttl_minutes=ttl_minutes
    )
    return send_html(to, subject, html, text)


def send_password_changed_email(to: str, *, org_name: str = "") -> bool:
    """Confirm a password change (security notification). True if the email was sent."""
    subject, html, text = tpl.password_changed(org_name=org_name)
    return send_html(to, subject, html, text)


def send_invite_email(
    to: str, invite_url: str, *, org_name: str = "", team_name: str = "", inviter: str = ""
) -> bool:
    """Notify an invited user with their join link. True if the email was sent."""
    where = f"the {team_name} team" if team_name else (org_name or "an organization")
    subject = (
        f"You're invited to {team_name} on Anthill" if team_name else "You're invited to Anthill"
    )
    body = "\n".join(
        [
            f"You've been invited to join {where} on Anthill"
            + (f" by {inviter}" if inviter else "")
            + ".",
            "",
            "Open this link to get started:",
            f"  {invite_url}",
            "",
            "Anthill is your organization's private, self-hosted assistant.",
        ]
    )
    return send_email(to, subject, body)
