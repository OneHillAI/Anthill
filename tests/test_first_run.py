"""First-run experience: solo-first setup (reusing the existing user model) and the
local-model readiness endpoint that drives the "preparing your local AI" banner.

The readiness endpoint's health() is mocked so the test does not depend on whether an
Ollama server happens to be running on the test machine.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch, *, seed_org=True):
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
    ids = {}
    if seed_org:
        s = app_mod._SessionFactory()
        org = Organization(name="Acme", slug="acme")
        s.add(org)
        s.flush()
        admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
        s.add(admin)
        s.commit()
        ids = {"org": org.id, "admin": admin.id}
    return TestClient(app_mod.app), app_mod, ids


def _auth(client, uid, org_id, role="admin"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


# ── solo-first setup (reuses the existing user/org model) ────────────────────────


def test_setup_solo_creates_a_local_profile(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch, seed_org=False)  # empty DB = first run
    r = client.post(
        "/setup",
        data={
            "admin_email": "me@example.com",
            "admin_password": "x" * 12,
            "admin_name": "Me",
            "topology": "solo",  # the new default
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert r.cookies.get("session_token")  # logged straight in, no separate sign-in
    from anthill.web.db import OrgSettings, User

    s = app_mod._SessionFactory()
    assert s.query(OrgSettings).first().deployment_topology == "solo"  # solo, not org
    user = s.query(User).first()
    assert user.email == "me@example.com" and user.hashed_password  # existing user model + password


# ── local-model readiness (drives the first-run banner) ──────────────────────────


def _status(client, app_mod, ids, monkeypatch, health_value):
    from anthill.inference.ollama import OllamaBackend

    monkeypatch.setattr(OllamaBackend, "health", lambda self, model=None: health_value)
    _auth(client, ids["admin"], ids["org"])
    return client.get("/local-model/status").json()


def test_status_ready_when_model_present(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    d = _status(client, app_mod, ids, monkeypatch, None)  # health None = reachable + model present
    assert d["ready"] is True and d["ollama_up"] is True


def test_status_pulling_when_model_missing(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    d = _status(client, app_mod, ids, monkeypatch, "Model 'qwen2.5:3b' not found. Run ollama pull.")
    assert d["ready"] is False and d["ollama_up"] is True  # engine up, model still downloading


def test_status_engine_down(tmp_path, monkeypatch):
    client, app_mod, ids = _app(tmp_path, monkeypatch)
    d = _status(
        client, app_mod, ids, monkeypatch, "Ollama not reachable at http://localhost:11434."
    )
    assert d["ready"] is False and d["ollama_up"] is False


def test_status_requires_login(tmp_path, monkeypatch):
    client, _m, _ids = _app(tmp_path, monkeypatch)
    assert client.get("/local-model/status", follow_redirects=False).status_code == 303
