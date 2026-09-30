"""Thumbs up/down on a chat answer and the matching TrainingExample's quality tier.

A thumbs-up promotes the matching example to "gold" (pre-existing). Thumbs-down was a dead end -
it recorded `ChatMessage.thumbs_up = False` for the satisfaction-% metric and did nothing else,
even when the matching example had already been promoted to gold or silver: a downvote couldn't
un-do an earlier upvote, or override an org-corroborated promotion, so a known-bad answer stayed
eligible for `export_jsonl`'s default `min_quality="silver"` floor. Thumbs-down now demotes a
gold/silver example back to bronze - below that floor - mirroring the upvote's promotion.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _setup(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import ChatMessage, Conversation, Organization, TrainingExample, User

    for k in ("ANTHILL_ORG_WIKI", "ANTHILL_WIKI_ROOT", "ANTHILL_WORKSPACE", "ANTHILL_FILES_DIR"):
        monkeypatch.setenv(k, str(tmp_path / k.lower()))
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
    user = User(org_id=org.id, email="a@x.com", role="member", active=True)
    s.add(user)
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, title="chat")
    s.add(conv)
    s.flush()
    msg = ChatMessage(conversation_id=conv.id, role="assistant", content="The answer.")
    s.add(msg)
    s.flush()
    ex = TrainingExample(
        org_id=org.id,
        instruction="the question",
        context="",
        output=msg.content,
        quality="bronze",
        scope="personal",
    )
    s.add(ex)
    s.commit()
    ids = {"org": org.id, "user": user.id, "conv": conv.id, "msg": msg.id, "ex": ex.id}
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    s.close()
    return app_mod, ids, client


def test_thumbs_up_promotes_the_matching_example_to_gold(tmp_path, monkeypatch):
    app_mod, ids, client = _setup(tmp_path, monkeypatch)
    from anthill.web.db import TrainingExample

    r = client.post(f"/chat/{ids['conv']}/thumbs", data={"message_id": ids["msg"], "value": 1})
    assert r.status_code == 200
    s = app_mod._SessionFactory()
    ex = s.get(TrainingExample, ids["ex"])
    assert ex.quality == "gold"
    assert ex.user_id == ids["user"]


def test_thumbs_down_demotes_a_gold_example_back_to_bronze(tmp_path, monkeypatch):
    app_mod, ids, client = _setup(tmp_path, monkeypatch)
    from anthill.web.db import TrainingExample

    s = app_mod._SessionFactory()
    s.get(TrainingExample, ids["ex"]).quality = "gold"
    s.commit()
    s.close()

    r = client.post(f"/chat/{ids['conv']}/thumbs", data={"message_id": ids["msg"], "value": -1})
    assert r.status_code == 200
    s = app_mod._SessionFactory()
    ex = s.get(TrainingExample, ids["ex"])
    assert ex.quality == "bronze"


def test_thumbs_down_demotes_a_silver_example_too(tmp_path, monkeypatch):
    app_mod, ids, client = _setup(tmp_path, monkeypatch)
    from anthill.web.db import TrainingExample

    s = app_mod._SessionFactory()
    s.get(TrainingExample, ids["ex"]).quality = "silver"
    s.commit()
    s.close()

    client.post(f"/chat/{ids['conv']}/thumbs", data={"message_id": ids["msg"], "value": -1})
    s = app_mod._SessionFactory()
    assert s.get(TrainingExample, ids["ex"]).quality == "bronze"


def test_thumbs_down_on_an_already_bronze_example_is_a_no_op(tmp_path, monkeypatch):
    app_mod, ids, client = _setup(tmp_path, monkeypatch)
    from anthill.web.db import TrainingExample

    r = client.post(f"/chat/{ids['conv']}/thumbs", data={"message_id": ids["msg"], "value": -1})
    assert r.status_code == 200
    s = app_mod._SessionFactory()
    ex = s.get(TrainingExample, ids["ex"])
    assert ex.quality == "bronze"
    assert ex.user_id is None  # untouched: nothing needed demoting


def test_message_thumbs_up_field_still_records_for_the_satisfaction_metric(tmp_path, monkeypatch):
    app_mod, ids, client = _setup(tmp_path, monkeypatch)
    from anthill.web.db import ChatMessage

    client.post(f"/chat/{ids['conv']}/thumbs", data={"message_id": ids["msg"], "value": -1})
    s = app_mod._SessionFactory()
    assert s.get(ChatMessage, ids["msg"]).thumbs_up is False
