"""Strict separation between a Solo account and an Org account (docs/specs/solo-org-separation.md).

An account type is its topology, not whether a backend is connected: a Solo account that connects its own
cloud endpoint stays Solo. It gets no org-only page, no Org chat, no org wiki scope, no org-plane agents and
no invites, while its own chats, personal wiki and model keep running on its endpoint. An Org account is
unchanged.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import (
    Agent,
    AgentIdentity,
    Conversation,
    Organization,
    OrgSettings,
    Team,
    TeamMembership,
    User,
)
from anthill.web.plane_routing import (
    PlaneUnavailable,
    account_plane,
    is_org_account,
    org_plane_ready,
    plane_inference,
    shares_org_model,
)

ENDPOINT = "https://gpu.example/v1"

# Every ORG-ONLY route of the table in the spec, as (method, concrete path).
ORG_ONLY = [
    ("GET", "/users"),
    ("POST", "/users/invite"),
    ("POST", "/users/2/role"),
    ("POST", "/users/2/deactivate"),
    ("POST", "/users/2/reset"),
    ("GET", "/settings/org"),
    ("GET", "/settings/general"),
    ("GET", "/settings/automation"),
    ("POST", "/settings/automation"),
    ("POST", "/settings/aws/test"),
    ("POST", "/settings/aws/provision-test"),
    ("GET", "/settings/slack"),
    ("POST", "/settings/slack"),
    ("GET", "/settings/discord"),
    ("POST", "/settings/discord"),
    ("GET", "/slack/oauth/start"),
    ("GET", "/slack/oauth/callback"),
    ("GET", "/settings/events"),
    ("POST", "/settings/events"),
    ("POST", "/settings/events/imap/test"),
    ("GET", "/settings/organization/wiki"),
    ("POST", "/settings/organization/wiki"),
    ("GET", "/settings/organization"),
    ("POST", "/settings/organization"),
    ("POST", "/settings/organization/cluster"),
    ("POST", "/settings/organization/name"),
    ("POST", "/settings/organization/aws"),
    ("POST", "/settings/organization/connect"),
    ("POST", "/settings/organization/discover-models"),
    ("POST", "/settings/organization/provision-key"),
    ("POST", "/settings/organization/hf-token"),
    ("POST", "/settings/organization/provision"),
    ("POST", "/settings/organization/teardown"),
    ("POST", "/settings/organization/council/provision"),
    ("POST", "/settings/organization/council/teardown"),
    ("POST", "/settings/organization/council/review-toggle"),
    ("GET", "/wiki/org"),
    ("GET", "/agent-access"),
    ("POST", "/agent-access/create"),
    ("POST", "/agent-access/1/scopes"),
    ("POST", "/agent-access/1/toggle"),
    ("POST", "/agent-access/1/mcp-token"),
    ("POST", "/memory/1/promote"),
]
# Install-level (host) controls: open sign-up, install-owner transfer, remote access and the Mac appliance.
# They decide things for the whole install, not for one organisation, so a Solo install owner keeps them.
INSTALL_LEVEL = [
    ("GET", "/settings/remote"),
    ("POST", "/settings/remote"),
    ("POST", "/settings/remote/stop"),
    ("GET", "/settings/remote/status"),
    ("POST", "/settings/signup"),
    ("POST", "/settings/install-owner"),
    ("GET", "/settings/appliance"),
    ("GET", "/settings/appliance/launchagent.plist"),
]


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cloud.aws.provision_test", lambda cfg: (False, "stubbed"))
    monkeypatch.setattr("anthill.web.imap_idle.test_connection", lambda *a, **k: (False, "stubbed"))

    def build(topology, *, endpoint=False, solo_compute="local"):
        eng = create_engine(
            f"sqlite:///{tmp_path / f'{topology}-{endpoint}-{solo_compute}.db'}",
            connect_args={"check_same_thread": False},
        )
        db_mod.create_tables(eng)
        app_mod._engine = eng
        app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
        s = app_mod._SessionFactory()
        org = Organization(name="Acme", slug="acme")
        s.add(org)
        s.flush()
        admin = User(org_id=org.id, email="admin@a.com", role="admin", active=True)
        member = User(org_id=org.id, email="member@a.com", role="member", active=True)
        s.add_all([admin, member])
        cfg = OrgSettings(org_id=org.id, deployment_topology=topology, solo_compute=solo_compute)
        if endpoint:
            cfg.org_backend_status = "validated"
            cfg.org_model_endpoint = ENDPOINT
            cfg.org_model = "big-model"
        s.add_all([cfg, AgentIdentity(org_id=org.id, name="ext")])
        s.commit()
        ids = {"org": org.id, "admin": admin.id, "member": member.id}
        s.close()

        def client(role=None):
            c = TestClient(app_mod.app, follow_redirects=False)
            if role:
                uid = ids["admin"] if role == "admin" else ids["member"]
                c.cookies.set("session_token", make_token(uid, ids["org"], role))
            return c

        return client, app_mod, ids

    yield build
    app_mod._engine = None
    app_mod._SessionFactory = None


def _send(client, method, path):
    return client.get(path) if method == "GET" else client.post(path, data={})


# ── every org-only route: 404 for a Solo account, unchanged for an Org account ──────────


@pytest.mark.parametrize(("method", "path"), ORG_ONLY)
def test_solo_admin_gets_404_on_org_only_route(make_app, method, path):
    client, _, _ = make_app("solo")
    assert _send(client("admin"), method, path).status_code == 404


@pytest.mark.parametrize(("method", "path"), ORG_ONLY)
def test_solo_admin_with_own_endpoint_gets_404_on_org_only_route(make_app, method, path):
    client, _, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    assert _send(client("admin"), method, path).status_code == 404


@pytest.mark.parametrize(("method", "path"), ORG_ONLY)
def test_org_admin_still_reaches_org_only_route(make_app, method, path):
    client, _, _ = make_app("org")
    r = _send(client("admin"), method, path)
    assert r.status_code != 404
    assert r.status_code != 403
    assert r.headers.get("location") != "/login"


@pytest.mark.parametrize(("method", "path"), ORG_ONLY)
def test_org_member_still_gets_403_on_org_only_route(make_app, method, path):
    client, _, _ = make_app("org")
    assert _send(client("member"), method, path).status_code == 403


@pytest.mark.parametrize(("method", "path"), ORG_ONLY)
def test_anonymous_is_still_sent_to_login(make_app, method, path):
    for topology in ("solo", "org"):
        client, _, _ = make_app(topology)
        r = _send(client(), method, path)
        assert (r.status_code, r.headers["location"]) == (303, "/login")


@pytest.mark.parametrize(("method", "path"), INSTALL_LEVEL)
def test_a_solo_install_owner_keeps_the_install_level_controls(make_app, method, path):
    client, _, _ = make_app("solo")
    assert _send(client("admin"), method, path).status_code != 404


@pytest.mark.parametrize(("method", "path"), INSTALL_LEVEL)
def test_install_level_controls_are_still_admin_only(make_app, method, path):
    for topology in ("solo", "org"):
        client, _, _ = make_app(topology)
        assert _send(client("member"), method, path).status_code == 403


def test_a_legacy_local_topology_counts_as_solo(make_app):
    client, app_mod, _ = make_app("org")
    s = app_mod._SessionFactory()
    s.query(OrgSettings).update({OrgSettings.deployment_topology: "local"})
    s.commit()
    assert client("admin").get("/users").status_code == 404
    assert not is_org_account(s.query(OrgSettings).first())


# ── routes the Solo account keeps ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/personalize",
        "/settings",
        "/models",
        "/models/pull-status",
        "/training",
        "/connectors/mcp",
        "/audit",
        "/metrics",
        "/teams",
        "/skills",
        "/contribute",
        "/wiki",
    ],
)
def test_solo_admin_keeps_the_shared_and_solo_pages(make_app, path):
    client, _, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    r = client("admin").get(path)
    assert r.status_code == 200, path


# ── a Solo account with its own validated endpoint is still Solo ──────────────────────


def test_the_topology_helpers():
    solo = type("C", (), {"deployment_topology": "solo", "org_backend_status": "validated"})()
    org = type("C", (), {"deployment_topology": "org", "org_backend_status": "validated"})()
    unset = type("C", (), {"deployment_topology": "", "org_backend_status": ""})()
    assert (is_org_account(solo), shares_org_model(solo), org_plane_ready(solo)) == (False,) * 3
    assert (is_org_account(org), shares_org_model(org), org_plane_ready(org)) == (True,) * 3
    assert is_org_account(unset) and not shares_org_model(unset)
    assert account_plane("org", solo) == "solo"
    assert account_plane("org", org) == "org"
    assert account_plane("team", solo) == "team"


def test_chat_new_org_on_a_solo_with_endpoint_falls_back_to_solo(make_app):
    client, app_mod, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    client("admin").post("/chat/new", data={"plane": "org"})
    s = app_mod._SessionFactory()
    assert s.query(Conversation).order_by(Conversation.id.desc()).first().plane == "solo"


def test_chat_new_org_on_an_org_account_is_unchanged(make_app):
    client, app_mod, _ = make_app("org", endpoint=True)
    client("admin").post("/chat/new", data={"plane": "org"})
    s = app_mod._SessionFactory()
    assert s.query(Conversation).order_by(Conversation.id.desc()).first().plane == "org"


def _chat_page(client, app_mod, org_id, user_id, plane="solo"):
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=org_id, user_id=user_id, plane=plane)
    s.add(conv)
    s.commit()
    return client.get(f"/chat/{conv.id}")


def test_solo_with_endpoint_has_no_org_chat_button_or_org_wiki_scope(make_app):
    client, app_mod, ids = make_app("solo", endpoint=True, solo_compute="cloud")
    page = _chat_page(client("admin"), app_mod, ids["org"], ids["admin"]).text
    assert 'class="btn rail-newchat rail-newchat-org"' not in page
    assert '<option value="org">' not in page
    assert "IS_ORG_MODE = false" in page


def test_org_account_keeps_the_org_chat_button_and_org_wiki_scope(make_app):
    client, app_mod, ids = make_app("org", endpoint=True)
    page = _chat_page(client("admin"), app_mod, ids["org"], ids["admin"]).text
    assert 'class="btn rail-newchat rail-newchat-org"' in page
    assert '<option value="org">Org wiki</option>' in page


def test_solo_with_endpoint_agent_form_offers_no_org_plane(make_app):
    client, app_mod, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    assert 'value="org"' not in client("admin").get("/agents").text
    client("admin").post("/agents/create", data={"name": "a", "plane": "org"})
    s = app_mod._SessionFactory()
    assert s.query(Agent).one().plane == "solo"


def test_org_account_agent_form_and_creation_are_unchanged(make_app):
    client, app_mod, _ = make_app("org", endpoint=True)
    assert 'value="org"' in client("admin").get("/agents").text
    client("admin").post("/agents/create", data={"name": "a", "plane": "org"})
    s = app_mod._SessionFactory()
    assert s.query(Agent).one().plane == "org"


def test_chat_agent_from_an_org_plane_chat_on_a_solo_is_a_solo_agent(make_app):
    client, app_mod, ids = make_app("solo", endpoint=True, solo_compute="cloud")
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=ids["org"], user_id=ids["admin"], plane="org")
    s.add(conv)
    s.commit()
    r = client("admin").post("/chat/agent", data={"mandate": "watch X", "conv_id": conv.id})
    assert r.status_code == 200
    assert s.query(Agent).one().plane == "solo"


def test_users_invite_is_refused_for_a_solo_with_endpoint(make_app):
    client, app_mod, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    r = client("admin").post("/users/invite", data={"email": "new@a.com"})
    assert r.status_code == 404
    assert app_mod._SessionFactory().query(User).filter(User.email == "new@a.com").count() == 0


def test_users_invite_on_an_org_account_still_invites(make_app, monkeypatch):
    monkeypatch.setattr("anthill.web.mailer.send_invite_email", lambda *a, **k: True)
    client, app_mod, _ = make_app("org", endpoint=True)
    r = client("admin").post("/users/invite", data={"email": "new@a.com"})
    assert r.status_code == 302 and "invited=new@a.com" in r.headers["location"]
    assert app_mod._SessionFactory().query(User).filter(User.email == "new@a.com").count() == 1


def _project(app_mod, ids):
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["org"], name="P", slug="p", owner_id=ids["admin"])
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=ids["admin"], role="owner", status="active"))
    s.commit()
    return team.id


def test_team_invite_is_refused_for_a_solo_with_endpoint(make_app):
    client, app_mod, ids = make_app("solo", endpoint=True, solo_compute="cloud")
    tid = _project(app_mod, ids)
    r = client("admin").post(f"/teams/{tid}/invite", data={"email": "member@a.com"})
    assert r.headers["location"] == f"/teams/{tid}?error=solo_no_members"
    s = app_mod._SessionFactory()
    assert s.query(TeamMembership).filter(TeamMembership.team_id == tid).count() == 1
    page = client("admin").get(f"/teams/{tid}").text
    assert "+ Invite member" not in page and "Solo project" in page


def test_team_invite_on_an_org_account_still_invites(make_app, monkeypatch):
    monkeypatch.setattr("anthill.web.mailer.send_invite_email", lambda *a, **k: True)
    client, app_mod, ids = make_app("org", endpoint=True)
    tid = _project(app_mod, ids)
    r = client("admin").post(f"/teams/{tid}/invite", data={"email": "member@a.com"})
    assert r.headers["location"] == f"/teams/{tid}?invited=member@a.com"
    assert "+ Invite member" in client("admin").get(f"/teams/{tid}").text


def test_solo_with_endpoint_cannot_reach_the_org_wiki_scope(make_app):
    client, _, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    c = client("admin")
    assert c.get("/wiki/page/x", params={"scope": "org"}).status_code == 404
    assert c.get("/wiki/raw/x", params={"scope": "org"}).status_code == 404
    assert c.get("/wiki/export.okgf.tgz").status_code == 404
    assert c.post("/wiki/principles", data={"scope": "org", "content": "x"}).status_code == 403


def test_solo_snippet_to_wiki_files_into_the_personal_wiki(make_app, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "anthill.web.app.propose_wiki_write",
        lambda db, **kw: seen.update(kw) or True,
    )
    client, app_mod, ids = make_app("solo", endpoint=True, solo_compute="cloud")
    from anthill.web.db import Snippet

    s = app_mod._SessionFactory()
    snip = Snippet(org_id=ids["org"], user_id=ids["admin"], content="Kafka is the pipeline.")
    s.add(snip)
    s.commit()
    client("admin").post(f"/snippets/{snip.id}/wiki", data={})
    assert seen["target_scope"] == "personal"


# ── the Solo account's own turns still run on its own endpoint ────────────────────────


def _cfg(topology, *, compute="cloud", status="validated", serve=""):
    return type(
        "C",
        (),
        {
            "deployment_topology": topology,
            "org_backend_status": status,
            "org_model_endpoint": ENDPOINT,
            "org_model": "big-model",
            "org_model_key_enc": "KEY",
            "org_provider": "",
            "org_provision_key_enc": "",
            "ollama_url": "",
            "ollama_model": "",
            "local_serve_url": serve,
            "local_serve_model": "",
            "solo_compute": compute,
        },
    )()


def _decrypt(token):
    return "PLAINTEXT-" + token


def test_solo_cloud_chat_still_runs_on_its_own_endpoint_with_personal_context():
    pi = plane_inference("solo", _cfg("solo"), decrypt=_decrypt)
    assert (pi.backend, pi.base_url, pi.model, pi.api_key) == (
        "openai",
        ENDPOINT,
        "big-model",
        "PLAINTEXT-KEY",
    )
    assert (pi.wiki_scope, pi.use_personal_context, pi.ephemeral) == ("personal", True, False)


def test_solo_with_endpoint_and_local_compute_stays_on_the_local_model():
    pi = plane_inference("solo", _cfg("solo", compute="local"), decrypt=_decrypt)
    assert pi.backend == "ollama" and pi.use_personal_context


def test_solo_cloud_project_chat_runs_on_its_own_endpoint_not_the_org_wiki():
    pi = plane_inference("team", _cfg("solo"), decrypt=_decrypt)
    assert (pi.backend, pi.base_url) == ("openai", ENDPOINT)
    assert (pi.wiki_scope, pi.use_personal_context) == ("team", True)


def test_solo_local_project_chat_with_an_endpoint_stays_on_the_local_model():
    pi = plane_inference("team", _cfg("solo", compute="local"), decrypt=_decrypt)
    assert pi.backend == "ollama" and pi.use_personal_context


def test_solo_cloud_project_chat_honours_use_local_now():
    pi = plane_inference("team", _cfg("solo"), decrypt=_decrypt, prefer_local=True)
    assert pi.backend == "ollama"


def test_org_account_project_chat_is_unchanged():
    pi = plane_inference("team", _cfg("org"), decrypt=_decrypt)
    assert (pi.backend, pi.base_url, pi.wiki_scope) == ("openai", ENDPOINT, "team")
    assert not pi.use_personal_context
    unconfigured = plane_inference("team", _cfg("org", status=""), decrypt=_decrypt)
    assert unconfigured.backend == "ollama"


def test_a_solo_per_turn_escalation_to_its_endpoint_still_resolves():
    pi = plane_inference("org", _cfg("solo", compute="local"), decrypt=_decrypt)
    assert (pi.plane, pi.base_url) == ("org", ENDPOINT)


def test_a_solo_escalated_turn_runs_on_its_endpoint_but_stays_a_solo_turn():
    pi = plane_inference("solo", _cfg("solo", compute="local"), decrypt=_decrypt, escalate=True)
    assert (pi.plane, pi.backend, pi.base_url) == ("solo", "openai", ENDPOINT)
    # Grounded in the account's own wiki with its personal context, never the org wiki or shared cache.
    assert (pi.wiki_scope, pi.use_personal_context, pi.ephemeral) == ("personal", True, False)


def test_a_solo_escalated_project_turn_keeps_the_project_wiki_and_personal_context():
    pi = plane_inference("team", _cfg("solo", compute="local"), decrypt=_decrypt, escalate=True)
    assert (pi.plane, pi.base_url, pi.wiki_scope, pi.use_personal_context) == (
        "team",
        ENDPOINT,
        "team",
        True,
    )


def test_a_solo_escalation_with_no_endpoint_says_so_instead_of_staying_local():
    cfg = _cfg("solo", compute="local", status="")
    with pytest.raises(PlaneUnavailable):
        plane_inference("solo", cfg, decrypt=_decrypt, escalate=True)


def test_a_solo_escalated_chat_turn_asks_for_a_solo_plane_with_escalation(make_app, monkeypatch):
    seen = []

    def spy(plane, cfg, **kw):
        seen.append((plane, kw.get("escalate")))
        raise PlaneUnavailable("stub: no model call in tests")

    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", spy)
    client, app_mod, ids = make_app("solo", endpoint=True, solo_compute="local")
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=ids["org"], user_id=ids["admin"], plane="solo")
    s.add(conv)
    s.commit()
    client("admin").get(f"/chat/{conv.id}/stream", params={"message": "hi", "escalate_org": "true"})
    assert seen == [("solo", True)]


def test_an_org_escalated_chat_turn_still_goes_to_the_org_plane(make_app, monkeypatch):
    seen = []

    def spy(plane, cfg, **kw):
        seen.append((plane, kw.get("escalate")))
        raise PlaneUnavailable("stub: no model call in tests")

    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", spy)
    client, app_mod, ids = make_app("org", endpoint=True)
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=ids["org"], user_id=ids["admin"], plane="solo")
    s.add(conv)
    s.commit()
    client("admin").get(f"/chat/{conv.id}/stream", params={"message": "hi", "escalate_org": "true"})
    assert seen == [("org", False)]


def test_a_stored_org_plane_chat_on_a_solo_streams_as_a_solo_chat(make_app, monkeypatch):
    planes_seen = []

    def spy(plane, cfg, **kw):
        planes_seen.append(plane)
        raise PlaneUnavailable("stub: no model call in tests")

    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", spy)
    client, app_mod, ids = make_app("solo", endpoint=True, solo_compute="cloud")
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=ids["org"], user_id=ids["admin"], plane="org")
    s.add(conv)
    s.commit()
    client("admin").get(f"/chat/{conv.id}/stream", params={"message": "hi"})
    assert planes_seen == ["solo"]


def test_a_stored_org_plane_chat_on_an_org_account_still_streams_as_org(make_app, monkeypatch):
    planes_seen = []

    def spy(plane, cfg, **kw):
        planes_seen.append(plane)
        raise PlaneUnavailable("stub: no model call in tests")

    monkeypatch.setattr("anthill.web.plane_routing.plane_inference", spy)
    client, app_mod, ids = make_app("org", endpoint=True)
    s = app_mod._SessionFactory()
    conv = Conversation(org_id=ids["org"], user_id=ids["admin"], plane="org")
    s.add(conv)
    s.commit()
    client("admin").get(f"/chat/{conv.id}/stream", params={"message": "hi"})
    assert planes_seen == ["org"]


def test_solo_with_endpoint_personal_wiki_still_works(make_app):
    client, _, _ = make_app("solo", endpoint=True, solo_compute="cloud")
    c = client("admin")
    assert c.get("/wiki").status_code == 200
    r = c.post("/wiki/principles", data={"scope": "personal", "content": "Be brief."})
    assert r.status_code in (200, 302, 303)
    assert "Be brief." in c.get("/wiki", params={"tab": "principles"}).text


def test_a_flagged_upload_banner_links_each_scope_to_its_own_review_queue():
    from pathlib import Path

    import anthill

    html = (Path(anthill.__file__).parent / "web" / "templates" / "wiki.html").read_text()
    line = next(row for row in html.splitlines() if "saved=='queued'" in row)
    assert "scope=='org' %}/wiki/review{% elif scope=='team' %}/teams/{{ team_id }}" in line
    assert "{% else %}/wiki/review/personal{% endif %}" in line
