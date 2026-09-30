"""Web push: VAPID key derivation + subscription storage (the testable parts).
End-to-end delivery needs a real browser + push service, so it isn't unit-tested.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web.db import PushSubscription, create_tables


def _db(tmp_path):
    from fk_seed import seed_org_and_users

    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    create_tables(engine)
    s = sessionmaker(bind=engine)()
    seed_org_and_users(s)
    s.commit()
    return s


def test_vapid_public_key_is_valid(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("py_vapid")  # VAPID derivation needs the optional [push] extra
    monkeypatch.setenv("ANTHILL_DB", str(tmp_path / "anthill.db"))
    import importlib

    from anthill.web import push as push_mod

    importlib.reload(push_mod)
    key = push_mod.public_key()
    assert isinstance(key, str) and len(key) >= 80  # base64url of a 65-byte EC point
    # persisted + stable across calls
    assert push_mod.public_key() == key
    assert (tmp_path / "vapid.json").exists()


def test_store_subscription_upserts(tmp_path):
    from anthill.web.push import store_subscription

    db = _db(tmp_path)
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "P", "auth": "A"}}
    store_subscription(db, org_id=1, user_id=1, sub=sub)
    store_subscription(db, org_id=1, user_id=1, sub=sub)  # same endpoint → upsert
    rows = db.query(PushSubscription).all()
    assert len(rows) == 1 and rows[0].endpoint == "https://push.example/abc"


def test_store_subscription_ignores_empty_endpoint(tmp_path):
    from anthill.web.push import store_subscription

    db = _db(tmp_path)
    store_subscription(db, org_id=1, user_id=1, sub={"keys": {}})
    assert db.query(PushSubscription).count() == 0


def test_send_to_user_no_subs_is_zero(tmp_path):
    from anthill.web.push import send_to_user

    db = _db(tmp_path)
    assert send_to_user(db, 999, {"title": "x"}) == 0
