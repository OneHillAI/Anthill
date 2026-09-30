import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web import discord_bot as dc
from anthill.web.db import DiscordApp, Organization, User


def _keypair():
    sk = Ed25519PrivateKey.generate()
    pub_hex = (
        sk.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    return sk, pub_hex


def _app(tmp_path, monkeypatch, *, guild_id="G1", enabled=True):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    monkeypatch.setattr(app_mod, "_spawn", lambda target, *a, **k: target(*a, **k))
    sk, pub_hex = _keypair()
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="ada@acme.com", display_name="Ada", role="admin", active=True))
    s.add(
        DiscordApp(
            org_id=org.id,
            application_id="APP1",
            public_key=pub_hex,
            guild_id=guild_id,
            enabled=enabled,
        )
    )
    s.commit()
    return app_mod, TestClient(app_mod.app), sk


def _post(client, sk, payload, *, sign=True):
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = sk.sign(ts.encode() + raw).hex() if sign else "00" * 64
    return client.post(
        "/discord/interactions",
        content=raw,
        headers={
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )


def _cmd(msg):
    return {
        "type": 2,
        "application_id": "APP1",
        "guild_id": "G1",
        "token": "tok",
        "data": {"name": "anthill", "options": [{"name": "message", "value": msg}]},
    }


def test_ping_returns_pong(tmp_path, monkeypatch):
    _, client, sk = _app(tmp_path, monkeypatch)
    r = _post(client, sk, {"type": 1, "application_id": "APP1"})
    assert r.status_code == 200 and r.json() == {"type": 1}


def test_bad_signature_is_401(tmp_path, monkeypatch):
    _, client, sk = _app(tmp_path, monkeypatch)
    r = _post(client, sk, {"type": 1, "application_id": "APP1"}, sign=False)
    assert r.status_code == 401


def test_member_idea_is_routed_to_intake(tmp_path, monkeypatch):
    from anthill.contribute.interaction import DISCLOSURE
    from anthill.web.db import ContributionProposal

    app_mod_, client, sk = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod_, "_backend_from_cfg", lambda cfg: None)
    monkeypatch.setattr(
        "anthill.contribute.distil_proposal",
        lambda idea, ref, backend: {
            "title": "Export the wiki to PDF",
            "kind": "feature",
            "spec": "...",
            "priority": "medium",
            "complete": True,
            "needs_detail": "",
        },
    )
    edits = []
    monkeypatch.setattr(dc, "edit_response", lambda app_id, token, content: edits.append(content))
    r = _post(client, sk, _cmd("feature request: export the wiki to PDF"))
    assert r.status_code == 200 and r.json()["type"] == 5  # deferred ack
    assert edits and edits[0].startswith(DISCLOSURE) and "intake" in edits[0].lower()
    s = app_mod_._SessionFactory()
    props = s.query(ContributionProposal).filter(ContributionProposal.source == "discord").all()
    s.close()
    assert len(props) == 1 and props[0].completeness == "complete"


def test_member_question_is_answered_with_disclosure(tmp_path, monkeypatch):
    from anthill.contribute.interaction import DISCLOSURE

    app_mod_, client, sk = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app_mod_, "_org_plane_answer", lambda db, org, member, q, **k: ("We deploy Tuesdays.", "m")
    )
    edits = []
    monkeypatch.setattr(dc, "edit_response", lambda app_id, token, content: edits.append(content))
    r = _post(client, sk, _cmd("when do we deploy?"))
    assert r.status_code == 200 and r.json()["type"] == 5
    assert edits and edits[0].startswith(DISCLOSURE) and "Tuesdays" in edits[0]


def test_wrong_guild_is_refused(tmp_path, monkeypatch):
    _, client, sk = _app(tmp_path, monkeypatch, guild_id="G1")
    payload = _cmd("hi")
    payload["guild_id"] = "OTHER"
    r = _post(client, sk, payload)
    assert r.status_code == 200 and "members server" in r.json()["data"]["content"]
