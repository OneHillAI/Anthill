"""The wiki's Files tab: every upload is preserved as an immutable raw copy (wiki/ingest.py's
preserve_raw_source) before being summarised into a page, but nothing in the UI listed those raw
files or let you get one back - filesystem only (founder, 2026-10-01: "where are uploaded docs
stored?"). Covers the listing and the download route, including its path-safety guards.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


class _FakeBackend:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, messages, **kw):
        return self._reply


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
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
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    other_org = Organization(name="Other", slug="other")
    s.add_all([member, other_org])
    s.flush()
    outsider = User(org_id=other_org.id, email="o@other.com", role="member", active=True)
    s.add(outsider)
    s.commit()
    ids = {"org": org.id, "member": member.id, "outsider": outsider.id, "other_org": other_org.id}

    monkeypatch.setattr(
        app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend('{"summary":"ok","flags":[]}')
    )
    return TestClient(app_mod.app), app_mod, ids


def _auth(client, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def _upload(client, name="onboarding.md", body=b"# Onboarding\n\nWe deploy on Fridays.\n"):
    return client.post(
        "/wiki/upload",
        data={"target_scope": "personal"},
        files={"file": (name, body, "text/markdown")},
        follow_redirects=False,
    )


def test_uploaded_file_is_listed_on_the_files_tab(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    assert _upload(client).status_code == 302

    r = client.get("/wiki", params={"tab": "files"})
    assert r.status_code == 200
    assert "onboarding" in r.text
    assert ".md" in r.text


def test_files_tab_is_empty_before_any_upload(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    r = client.get("/wiki", params={"tab": "files"})
    assert r.status_code == 200
    assert "No files yet" in r.text


def test_raw_file_downloads_with_original_content(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    body = b"# Onboarding\n\nWe deploy on Fridays.\n"
    _upload(client, body=body)

    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("personal", user_id=ids["member"])
    [raw_name] = [p.name for p in ws.raw.iterdir()]

    r = client.get(f"/wiki/raw/{raw_name}", params={"scope": "personal"})
    assert r.status_code == 200
    assert r.content == body
    assert "attachment" in r.headers["content-disposition"]


def test_raw_file_download_rejects_path_traversal(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    _upload(client)

    r = client.get("/wiki/raw/..%2F..%2Fetc%2Fpasswd", params={"scope": "personal"})
    assert r.status_code == 404


def test_raw_file_download_404s_for_an_unknown_name(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])

    r = client.get("/wiki/raw/does-not-exist.md", params={"scope": "personal"})
    assert r.status_code == 404


def test_raw_file_download_is_scoped_per_user(tmp_path, monkeypatch):
    """Personal scope resolves to the *caller's* own workspace (_wiki_ws keys it off the session
    user), so another org's member can never reach someone else's raw file by guessing its name -
    they only ever see their own (empty) personal workspace."""
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"])
    _upload(client)

    from anthill.wiki.workspace import workspace_for

    ws = workspace_for("personal", user_id=ids["member"])
    [raw_name] = [p.name for p in ws.raw.iterdir()]

    _auth(client, ids["outsider"], ids["other_org"])
    r = client.get(f"/wiki/raw/{raw_name}", params={"scope": "personal"})
    assert r.status_code == 404
