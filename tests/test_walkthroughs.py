"""Per-surface walkthrough completion state (#683): independent from the global
onboarding tour (test_onboarding.py) and from each other. The walkthrough UI itself
is client-side (static/walkthrough.js); here we cover the server-side status/done
round trip.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, User


def _client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return c, app_mod, user.id, org.id


def test_walkthrough_starts_undone_and_persists_completion(tmp_path):
    c, app_mod, uid, _ = _client(tmp_path)
    try:
        assert c.get("/walkthroughs/status", params={"surface": "wiki"}).json()["done"] is False
        assert c.post("/walkthroughs/done", data={"surface": "wiki"}).json()["ok"] is True
        assert c.get("/walkthroughs/status", params={"surface": "wiki"}).json()["done"] is True

        s = app_mod._SessionFactory()
        u = s.get(User, uid)
        assert u.completed_walkthroughs == "wiki"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_completing_one_surface_does_not_complete_another(tmp_path):
    c, app_mod, _, _ = _client(tmp_path)
    try:
        c.post("/walkthroughs/done", data={"surface": "memory"})
        assert c.get("/walkthroughs/status", params={"surface": "memory"}).json()["done"] is True
        assert c.get("/walkthroughs/status", params={"surface": "wiki"}).json()["done"] is False
        assert c.get("/walkthroughs/status", params={"surface": "skills"}).json()["done"] is False
        assert c.get("/walkthroughs/status", params={"surface": "snippets"}).json()["done"] is False
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_multiple_completions_accumulate_in_the_csv_column(tmp_path):
    c, app_mod, uid, _ = _client(tmp_path)
    try:
        c.post("/walkthroughs/done", data={"surface": "wiki"})
        c.post("/walkthroughs/done", data={"surface": "skills"})
        s = app_mod._SessionFactory()
        u = s.get(User, uid)
        assert set(u.completed_walkthroughs.split(",")) == {"wiki", "skills"}
        assert c.get("/walkthroughs/status", params={"surface": "wiki"}).json()["done"] is True
        assert c.get("/walkthroughs/status", params={"surface": "skills"}).json()["done"] is True
        assert c.get("/walkthroughs/status", params={"surface": "memory"}).json()["done"] is False
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_knowledge_hub_is_a_recognized_walkthrough_surface(tmp_path):
    # The hub-level self-explanation reuses the completion API under surface "knowledge-hub": it starts
    # undone (so it auto-shows once), persists completion, and is independent of the per-surface tours.
    c, app_mod, _, _ = _client(tmp_path)
    try:
        assert (
            c.get("/walkthroughs/status", params={"surface": "knowledge-hub"}).json()["done"]
            is False
        )
        c.post("/walkthroughs/done", data={"surface": "knowledge-hub"})
        assert (
            c.get("/walkthroughs/status", params={"surface": "knowledge-hub"}).json()["done"]
            is True
        )
        # dismissing the hub explainer does not complete a per-surface tour
        assert c.get("/walkthroughs/status", params={"surface": "wiki"}).json()["done"] is False
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_knowledge_hub_leads_and_per_surface_tours_are_on_demand(tmp_path):
    # The Knowledge hub renders one hub-level explainer + a light per-tab hint; the per-surface tour no
    # longer auto-starts (it is registered {autostart:false}, still replayable from "Take a tour").
    c, app_mod, _, _ = _client(tmp_path)
    try:
        body = c.get("/wiki").text
        assert 'id="know-intro"' in body and "This is your Living wiki" in body  # the hub explainer
        assert 'class="know-hint"' in body  # the light per-tab hint
        assert "{autostart: false}" in body  # the wiki tour is demoted to on-demand
        assert "_anthillWalkthroughs.wiki.start()" in body  # ...still replayable via Take a tour
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_unknown_surface_is_reported_done_and_never_persisted(tmp_path):
    # An unrecognized surface key can't ever be marked "not done" (nothing to walk through), and
    # a done-post for one is silently ignored rather than polluting the CSV column.
    c, app_mod, uid, _ = _client(tmp_path)
    try:
        assert c.get("/walkthroughs/status", params={"surface": "chat"}).json()["done"] is True
        c.post("/walkthroughs/done", data={"surface": "chat"})
        s = app_mod._SessionFactory()
        u = s.get(User, uid)
        assert u.completed_walkthroughs == ""
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
