from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import DiscordApp, Organization, OrgSettings, User


def _admin(tmp_path, monkeypatch):
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
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add_all([u, OrgSettings(org_id=o.id)])
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


def test_discord_settings_page_renders(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    page = c.get("/settings/discord").text
    assert "/discord/interactions" in page and 'name="application_id"' in page


def test_saving_discord_config_enables_the_bot(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    r = c.post(
        "/settings/discord",
        data={"application_id": "APP1", "public_key": "ab12", "guild_id": "G1", "enabled": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "saved=1" in r.headers["location"]
    s = app_mod._SessionFactory()
    cfg = s.query(DiscordApp).filter(DiscordApp.org_id == org_id).first()
    s.close()
    assert cfg.application_id == "APP1" and cfg.guild_id == "G1" and cfg.enabled is True


def test_cannot_enable_without_all_fields(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    r = c.post(
        "/settings/discord",
        data={"application_id": "APP1", "enabled": "true"},  # no public_key / guild_id
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=need_fields" in r.headers["location"]
    s = app_mod._SessionFactory()
    cfg = s.query(DiscordApp).filter(DiscordApp.org_id == org_id).first()
    s.close()
    assert cfg is not None and cfg.enabled is False  # saved, but not enabled


def test_bot_token_registers_command_and_is_not_stored(tmp_path, monkeypatch):
    from anthill.web import discord_bot as dc

    c, _app_mod, _org_id = _admin(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        dc,
        "register_command",
        lambda app_id, guild_id, token: (calls.append((app_id, guild_id, token)), (True, "ok"))[1],
    )
    r = c.post(
        "/settings/discord",
        data={
            "application_id": "APP1",
            "public_key": "ab12",
            "guild_id": "G1",
            "enabled": "true",
            "bot_token": "secrettoken",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "registered=ok" in r.headers["location"]
    assert calls == [("APP1", "G1", "secrettoken")]
    # the token is used once, never persisted (DiscordApp has no token column)
    assert not hasattr(DiscordApp, "bot_token_enc")
