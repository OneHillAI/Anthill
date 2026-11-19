"""Settings links the Backup page for an admin, Solo included (docs/specs/solo-backup-link.md, #301).

The Backup page is open to a Solo owner, but nothing a Solo account saw linked to it, and the desktop app has no
address bar. Model-free: pages are rendered with a TestClient.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app), app_mod


def _device_tab(page: str) -> str:
    start = page.index('data-st="device"', page.index('class="st-panel"'))
    return page[start : page.index('class="st-panel"', start + 1)]


def test_a_solo_account_finds_backup_under_this_device(tmp_path, monkeypatch):
    client, _app_mod = _app(tmp_path, monkeypatch)
    r = client.post(  # first run: a Solo account, as the desktop app creates it
        "/setup",
        data={
            "admin_email": "me@example.com",
            "admin_password": "x" * 12,
            "admin_name": "Me",
            "topology": "solo",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.cookies.get("session_token")
    device = _device_tab(client.get("/personalize").text)
    assert '<a href="/backup"' in device
    assert "Back up and restore" in device
    assert "passphrase-protected file that carries your key" in device


def test_a_member_who_is_not_an_admin_gets_no_backup_link(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    client, app_mod = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add(member)
    s.commit()
    client.cookies.set("session_token", make_token(member.id, org.id, "member"))
    page = client.get("/personalize").text
    assert 'href="/backup"' not in page
