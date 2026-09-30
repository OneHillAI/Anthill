"""Branded HTML + plain-text bodies for Anthill's transactional emails.

Standard library only (no template engine) so the mailer stays dependency-light and sovereign. Each
builder returns ``(subject, html, text)``; the mailer wraps them into a multipart/alternative message.
Styles are inline because most mail clients strip ``<style>`` blocks.
"""

from __future__ import annotations

from html import escape

_ACCENT = "#D4891A"


def _shell(
    title: str, intro_html: str, *, cta_label: str = "", cta_url: str = "", outro_html: str = ""
) -> str:
    """Wrap body content in the branded HTML layout (table-based for email-client compatibility)."""
    button = ""
    link_fallback = ""
    if cta_label and cta_url:
        button = (
            f'<tr><td style="padding:8px 0 18px"><a href="{escape(cta_url)}" '
            f'style="display:inline-block;background:{_ACCENT};color:#1a1a1a;text-decoration:none;'
            f'font-weight:600;padding:12px 22px;border-radius:8px">{escape(cta_label)}</a></td></tr>'
        )
        link_fallback = (
            '<tr><td style="padding:0 0 6px;color:#8a8a8a;font-size:13px">Or paste this link into your '
            f'browser:<br><span style="color:#555;word-break:break-all">{escape(cta_url)}</span></td></tr>'
        )
    outro = (
        f'<tr><td style="font-size:15px;line-height:1.55;color:#333">{outro_html}</td></tr>'
        if outro_html
        else ""
    )
    return (
        '<!DOCTYPE html><html><body style="margin:0;background:#f4f2ee;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#222">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f2ee;padding:24px 0"><tr><td align="center">'
        '<table role="presentation" width="480" cellpadding="0" cellspacing="0" '
        'style="background:#fff;border-radius:14px;padding:32px;max-width:480px;text-align:left">'
        '<tr><td style="padding-bottom:6px;font-size:22px;font-weight:700">'
        f'Ant<span style="color:{_ACCENT}">hill</span></td></tr>'
        f'<tr><td style="padding:6px 0 14px;font-size:19px;font-weight:600">{escape(title)}</td></tr>'
        f'<tr><td style="font-size:15px;line-height:1.55;color:#333">{intro_html}</td></tr>'
        f"{button}{link_fallback}{outro}"
        '<tr><td style="padding-top:22px;color:#9a9a9a;font-size:12px;line-height:1.5;'
        "border-top:1px solid #eee\">Anthill - your organization's private, self-hosted AI. "
        "This is an automated message; reply to reach your admin.</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def welcome(*, name: str = "", org_name: str = "", url: str = "") -> tuple[str, str, str]:
    who = f" {name}" if name else ""
    where = f" for {org_name}" if org_name else ""
    subject = f"Welcome to {org_name}" if org_name else "Welcome to Anthill"
    lead = (
        f"Hi{who}, your Anthill account{where} is ready. Anthill is your organization's private, "
        "self-hosted AI - your chat, wiki, and models stay on your own infrastructure."
    )
    html = _shell(
        "Welcome to Anthill",
        f"<p style='margin:0 0 14px'>{escape(lead)}</p>",
        cta_label="Open Anthill" if url else "",
        cta_url=url,
        outro_html="<p style='margin:0'>Questions? Just reply to this email to reach your admin.</p>",
    )
    text_lines = [f"Hi{who}, your Anthill account{where} is ready.", ""]
    text_lines.append(
        "Anthill is your organization's private, self-hosted AI - your chat, wiki, and models stay "
        "on your own infrastructure."
    )
    if url:
        text_lines += ["", f"Open Anthill: {url}"]
    text_lines += ["", "Questions? Reply to this email to reach your admin."]
    return subject, html, "\n".join(text_lines)


def verify_email(*, verify_url: str, name: str = "", org_name: str = "") -> tuple[str, str, str]:
    who = f" {name}" if name else ""
    where = f" for {org_name}" if org_name else ""
    subject = f"Confirm your email for {org_name}" if org_name else "Confirm your email for Anthill"
    lead = (
        f"Hi{who}, you just created an Anthill account{where}. Confirm this email address to "
        "activate your account and sign in."
    )
    html = _shell(
        "Confirm your email",
        f"<p style='margin:0 0 14px'>{escape(lead)}</p>",
        cta_label="Confirm my email",
        cta_url=verify_url,
        outro_html=(
            "<p style='margin:0'>If you didn't create this account, you can ignore this email - "
            "nothing will be activated.</p>"
        ),
    )
    text = "\n".join(
        [
            f"Hi{who}, you just created an Anthill account{where}.",
            "",
            "Confirm this email address to activate your account and sign in:",
            f"  {verify_url}",
            "",
            "If you didn't create this account, you can ignore this email - nothing will be activated.",
        ]
    )
    return subject, html, text


def password_reset(
    *, reset_url: str, org_name: str = "", ttl_minutes: int = 60
) -> tuple[str, str, str]:
    where = f" for {org_name}" if org_name else ""
    subject = "Reset your Anthill password"
    lead = f"A password reset was requested for your Anthill account{where}."
    html = _shell(
        "Reset your password",
        f"<p style='margin:0 0 14px'>{escape(lead)} Choose a new password with the button below.</p>",
        cta_label="Choose a new password",
        cta_url=reset_url,
        outro_html=(
            f"<p style='margin:0'>The link expires in {ttl_minutes} minutes and can be used once. "
            "If you didn't request this, you can ignore this email - your password is unchanged.</p>"
        ),
    )
    text = "\n".join(
        [
            f"{lead}",
            "",
            "Open this link to choose a new password:",
            f"  {reset_url}",
            "",
            f"The link expires in {ttl_minutes} minutes and can be used once.",
            "If you didn't request this, you can ignore this email - your password is unchanged.",
        ]
    )
    return subject, html, text


def password_changed(*, org_name: str = "") -> tuple[str, str, str]:
    where = f" for {org_name}" if org_name else ""
    subject = "Your Anthill password was changed"
    lead = f"This confirms that the password for your Anthill account{where} was just changed."
    html = _shell(
        "Your password was changed",
        f"<p style='margin:0 0 14px'>{escape(lead)}</p>",
        outro_html=(
            "<p style='margin:0'>If this was you, no action is needed. <b>If you did not change your "
            "password</b>, contact your administrator immediately - your account may be at risk.</p>"
        ),
    )
    text = "\n".join(
        [
            f"{lead}",
            "",
            "If this was you, no action is needed.",
            "If you did NOT change your password, contact your administrator immediately - your "
            "account may be at risk.",
        ]
    )
    return subject, html, text
