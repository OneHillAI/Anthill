"""The chat "Web search" toggle only ever affected the plain-chat path's own web augmentation
(decide_web/auto_web) - agent mode (explicit or the router's silent agent_auto, #421) kept
web_search/fetch_url available to the model regardless, so turning the toggle off did not actually
stop the model from reaching the internet once a turn went through agent mode. make_tools() now
takes an `exclude` set (WEB_TOOLS for the off case), and chat_stream wires it to that turn's
web_effective flag - the same request-scoped flag already used by the non-agent path, not the
persisted, org-wide AgentPrincipal/scopes governance (which serves a different purpose and must not
be mutated by one user's per-message checkbox state).
"""

from anthill.agent.tools import WEB_TOOLS, make_tools


def test_web_tools_are_present_by_default():
    names = {t.name for t in make_tools(workspace="/tmp/x")}
    assert "web_search" in names
    assert "fetch_url" in names


def test_excluding_web_tools_drops_exactly_those_two():
    names = {t.name for t in make_tools(workspace="/tmp/x", exclude=WEB_TOOLS)}
    assert "web_search" not in names
    assert "fetch_url" not in names
    # everything else survives - this isn't "local model only", just "no internet"
    assert "search_wiki" in names
    assert "read_wiki" in names
    assert "create_file" in names
    assert "remember" in names


def test_web_tools_constant_matches_the_existing_web_scope():
    from anthill.agent.tools import tool_scope

    for name in WEB_TOOLS:
        assert tool_scope(name) == "web"


# ── /chat/{id}/stream: the toggle actually reaches agent mode's tool list ──────────────────────────


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, OrgSettings, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@a.com", role="member", active=True)
    s.add_all(
        [
            user,
            OrgSettings(
                org_id=org.id,
                deployment_topology="solo",
                local_serve_url="http://127.0.0.1:11435/v1",
                local_serve_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
            ),
        ]
    )
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    s.add(conv)
    s.commit()

    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, conv.id


class _FakeExecutor:
    captured_tools = None

    def __init__(self, backend, tools, **kw):
        _FakeExecutor.captured_tools = tools

    def stream(self, goal, *, context=""):
        yield "ok"


def test_agent_mode_excludes_web_tools_when_the_toggle_is_off(tmp_path, monkeypatch):
    import anthill.agent.executor as executor_mod

    client, _app_mod, conv_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(executor_mod, "AgentExecutor", _FakeExecutor)

    resp = client.get(
        f"/chat/{conv_id}/stream",
        params={"message": "plan my week", "agent_mode": "true", "web": "false"},
    )
    assert resp.status_code == 200
    names = {t.name for t in _FakeExecutor.captured_tools}
    assert "web_search" not in names
    assert "fetch_url" not in names
    assert "search_wiki" in names  # non-web tools are unaffected


def test_agent_mode_keeps_web_tools_when_the_toggle_is_on(tmp_path, monkeypatch):
    import anthill.agent.executor as executor_mod

    client, _app_mod, conv_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(executor_mod, "AgentExecutor", _FakeExecutor)

    resp = client.get(
        f"/chat/{conv_id}/stream",
        params={"message": "plan my week", "agent_mode": "true", "web": "true"},
    )
    assert resp.status_code == 200
    names = {t.name for t in _FakeExecutor.captured_tools}
    assert "web_search" in names
    assert "fetch_url" in names
