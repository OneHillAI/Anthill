"""Solo tuning: a Solo account (deployment_topology == "solo") fine-tunes its own model on its PERSONAL
gold; an org trains on org-scope gold only. Reuses the existing pipeline (docs/specs/solo-tuning.md)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, TrainingExample, User


def _session(tmp_path):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    seed_org_and_users(s)
    s.commit()
    return s


def _seed_gold(s, org_id, scope, n):
    for i in range(n):
        s.add(
            TrainingExample(
                org_id=org_id,
                user_id=1,
                instruction=f"q{i}",
                output=f"a{i}",
                scope=scope,
                quality="gold",
            )
        )
    s.commit()


def test_gold_uses_personal_scope_for_a_solo_account(tmp_path):
    from anthill.training.executor import _gold

    s = _session(tmp_path)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology="solo"))
    _seed_gold(s, org.id, "personal", 3)
    _seed_gold(s, org.id, "org", 5)
    cfg = s.query(OrgSettings).first()
    rows = _gold(s, org.id, cfg)
    assert len(rows) == 3 and all(r.scope == "personal" for r in rows)  # solo -> personal gold


def test_gold_uses_org_scope_for_an_org_account(tmp_path):
    from anthill.training.executor import _gold

    s = _session(tmp_path)
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology="org"))
    _seed_gold(s, org.id, "personal", 3)
    _seed_gold(s, org.id, "org", 5)
    cfg = s.query(OrgSettings).first()
    rows = _gold(s, org.id, cfg)
    assert len(rows) == 5 and all(r.scope == "org" for r in rows)  # org -> org-scope gold only


# ── web surface ──────────────────────────────────────────────────────────────────────


def _solo_admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add_all(
        [
            User(org_id=org.id, email="a@a.com", role="admin", active=True),
            OrgSettings(org_id=org.id, deployment_topology="solo"),  # a Solo account
        ]
    )
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(s.query(User).first().id, org.id, "admin"))
    return c, app_mod, org.id


def test_solo_settings_shows_the_tuning_section(tmp_path, monkeypatch):
    # The full "ready" card only renders when local training can actually run here (see
    # test_solo_cloud_training.py's not-ready coverage for the CI-default, no-mlx-lm case) -
    # simulate an Apple-Silicon dev box with mlx-lm installed.
    from anthill.training.backends.onprem import OnPremBackend

    monkeypatch.setattr(OnPremBackend, "validate", lambda self, cfg: (True, "ready"))
    c, app_mod, org_id = _solo_admin(tmp_path, monkeypatch)
    _seed_gold(app_mod._SessionFactory(), org_id, "personal", 2)
    page = c.get("/personalize").text
    assert "Self-tuning" in page and "trainNow()" in page  # the Solo self-tuning section + action
    assert 'id="tune-count">2<' in page  # the personal gold count


def test_training_run_uses_personal_scope_for_a_solo_account(tmp_path, monkeypatch):
    # A Solo account trains on PERSONAL gold: org-scope gold present but no personal gold -> nothing to
    # train on (proves the scope is personal, not org).
    c, app_mod, org_id = _solo_admin(tmp_path, monkeypatch)
    _seed_gold(app_mod._SessionFactory(), org_id, "org", 5)  # org gold only
    r = c.post("/training/run", follow_redirects=False)
    assert r.json()["ok"] is False  # no PERSONAL gold -> nothing to train on for a Solo account


def test_training_run_starts_on_personal_gold(tmp_path, monkeypatch):
    import anthill.training.executor as ex

    monkeypatch.setattr(ex, "run_scheduled", lambda *a, **k: 0)  # do not spawn a real training run
    c, app_mod, org_id = _solo_admin(tmp_path, monkeypatch)
    _seed_gold(app_mod._SessionFactory(), org_id, "personal", 8)
    r = c.post("/training/run", follow_redirects=False)
    assert r.json()["ok"] is True  # personal gold present -> a run starts
