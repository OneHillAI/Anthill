"""Event-driven proactivity decision logic (actions). Training stays batched."""

from datetime import datetime, timedelta, timezone

from anthill.web.events import inbox_files, should_drain

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


def test_event_mode_acts_immediately_when_pending():
    go, reason = should_drain(
        "event", has_files=True, last_run=NOW - timedelta(seconds=1), interval_s=300, now=NOW
    )
    assert go and "event" in reason


def test_no_action_when_nothing_pending():
    go, reason = should_drain("event", has_files=False, last_run=None, interval_s=300, now=NOW)
    assert not go and "nothing" in reason


def test_scheduled_waits_for_interval():
    go, reason = should_drain(
        "scheduled", has_files=True, last_run=NOW - timedelta(seconds=60), interval_s=300, now=NOW
    )
    assert not go and "within interval" in reason


def test_scheduled_fires_after_interval():
    go, _ = should_drain(
        "scheduled", has_files=True, last_run=NOW - timedelta(seconds=600), interval_s=300, now=NOW
    )
    assert go


def test_scheduled_first_run_fires():
    go, _ = should_drain("scheduled", has_files=True, last_run=None, interval_s=300, now=NOW)
    assert go


def test_inbox_files_lists_only_real_files(tmp_path):
    ws = tmp_path / "ws"
    (ws / "inbox").mkdir(parents=True)
    (ws / "inbox" / "note.md").write_text("hi")
    (ws / "inbox" / ".hidden").write_text("x")  # ignored
    (ws / "inbox" / "sub").mkdir()  # dir ignored
    files = inbox_files(str(ws))
    assert [f.name for f in files] == ["note.md"]


def test_inbox_files_empty_when_no_inbox(tmp_path):
    assert inbox_files(str(tmp_path / "nope")) == []


def test_unprocessable_pdf_is_set_aside_and_surfaced(tmp_path):
    """A PDF the drain can never complete must leave the inbox, so it stops being
    retried at full parse cost on every tick, and must stay visible to the user."""
    from anthill.web.scheduler import _set_aside

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "huge.pdf"
    source.write_bytes(b"%PDF-1.4")
    surfaced = []

    _set_aside(source, "needs-confirmation", "scheduler", lambda *a: surfaced.append(a), "80 pages")

    assert not source.exists()
    aside = inbox / "needs-confirmation" / "huge.pdf"
    assert aside.read_bytes() == b"%PDF-1.4"
    assert inbox_files(str(tmp_path)) == []  # the next tick no longer picks it up
    _identity, _tool, scope, allowed = surfaced[0]
    assert not allowed and "huge.pdf" in scope and "80 pages" in scope
