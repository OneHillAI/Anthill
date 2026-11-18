"""Settings says when a change is waiting for Save (docs/specs/settings-one-nav-and-unsaved-note.md).

Nothing on the Settings page saves until Save is pressed, so the page shows "No unsaved changes" and switches to
"You have unsaved changes" when a field or switch is changed. Model-free: pages are rendered with a TestClient.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="admin", active=True)
    s.add(user)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client


def test_the_save_bar_says_whether_a_change_is_waiting(tmp_path, monkeypatch):
    page = _client(tmp_path, monkeypatch).get("/personalize").text
    assert 'id="settings-form"' in page
    bar = page[
        page.index('id="set-savebar"') : page.index("</form>", page.index('id="set-savebar"'))
    ]
    assert 'id="set-dirty"' in bar and "No unsaved changes" in bar
    assert 'role="status"' in bar and 'aria-live="polite"' in bar
    # the script compares the form with how it loaded, flips the words, and asks before leaving
    assert "You have unsaved changes" in page
    assert "addEventListener('beforeunload'" in page


def test_the_thinking_row_is_one_short_description(tmp_path, monkeypatch):
    page = _client(tmp_path, monkeypatch).get("/personalize").text
    row = page[page.index('<span class="setrow-name">Thinking</span>') :]
    row = row[: row.index("</span></div>")]
    assert "Off by default for a new account. Only affects models running on this machine." in row
    assert "Web search has its own default" not in row  # that belongs to the Privacy tab
    assert len(row) < 330
