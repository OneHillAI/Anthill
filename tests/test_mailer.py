"""Invite email: graceful no-op when SMTP isn't configured (the alpha default)."""

from anthill.web import mailer

_SMTP_ENV = (
    "ANTHILL_SMTP_HOST",
    "ANTHILL_SMTP_FROM",
    "ANTHILL_SMTP_USER",
    "ANTHILL_SMTP_PASSWORD",
    "ANTHILL_SMTP_PORT",
)


def _clear(monkeypatch):
    for k in _SMTP_ENV:
        monkeypatch.delenv(k, raising=False)


def test_unconfigured_smtp_returns_false(monkeypatch):
    _clear(monkeypatch)
    assert mailer.smtp_configured() is False
    assert mailer.send_email("a@b.com", "s", "body") is False


def test_send_invite_email_false_without_smtp(monkeypatch):
    _clear(monkeypatch)
    assert mailer.send_invite_email("a@b.com", "http://x/invite/1", team_name="P") is False


def test_smtp_configured_true_when_set(monkeypatch):
    monkeypatch.setenv("ANTHILL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ANTHILL_SMTP_FROM", "anthill@example.com")
    assert mailer.smtp_configured() is True
