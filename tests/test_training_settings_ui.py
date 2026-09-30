"""Training UI: the Training data page has no separate provider picker - the fine-tune follows the org
cloud (set on the Cloud & model page) and the page only carries the train toggle + operational settings."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db
from anthill.web.db import Organization, User


def _admin_client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c


def test_training_section_follows_the_org_cloud_no_separate_picker(tmp_path):
    c = _admin_client(tmp_path)
    body = c.get("/training").text
    assert 'name="training_backend"' not in body  # no separate training provider dropdown
    assert 'name="training_provider"' not in body  # no neocloud (RunPod/Modal) sub-dropdown
    assert 'name="training_enabled"' in body  # the train toggle
    assert "/settings/organization" in body  # it points at the org cloud
    # the card is gone from Settings entirely
    assert "Training &amp; backend hosting" not in c.get("/settings").text
