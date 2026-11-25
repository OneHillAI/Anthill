"""Every new user is invited once to the early-adopter Slack group, in the notification bell (#369). Model-free."""

from datetime import datetime

from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import community
from anthill.web import db as db_mod
from anthill.web.db import Notification, Organization, OrgSettings, User

AFTER = datetime(2026, 12, 3, 9, 0)
BEFORE = datetime(2026, 11, 20, 9, 0)


def _client(tmp_path, monkeypatch, *, created_at=AFTER):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    user = User(
        org_id=org.id, email="u@acme.com", role="member", active=True, created_at=created_at
    )
    s.add_all([user, OrgSettings(org_id=org.id)])
    s.commit()
    uid, org_id = user.id, org.id
    s.close()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, org_id, "member"))
    return client, app_mod, uid


def _invites(app_mod, uid):
    s = app_mod._SessionFactory()
    try:
        return s.query(Notification).filter_by(user_id=uid, kind=community.KIND).all()
    finally:
        s.close()


def test_a_new_user_gets_the_invitation_once_with_the_exact_words_and_link(tmp_path, monkeypatch):
    client, app_mod, uid = _client(tmp_path, monkeypatch)
    client.get("/")
    (n,) = _invites(app_mod, uid)
    assert n.title == "Join the early-adopter Slack group"
    assert n.body == (
        "Join the early-adopter Slack group to receive support, provide feedback, and steer the "
        "development of Anthill."
    )
    assert n.link == (
        "https://join.slack.com/t/onehill/shared_invite/zt-4eozypon9-tw3bvOaf_6MGwUChSgZCZw"
    )
    assert n.read is False


def test_it_is_not_given_again_on_later_pages_or_by_two_calls_in_a_row(tmp_path, monkeypatch):
    client, app_mod, uid = _client(tmp_path, monkeypatch)
    for url in ("/", "/", "/notifications", "/"):
        client.get(url)
    s = app_mod._SessionFactory()
    community.ensure_invite(s, user_id=uid, org_id=None)
    community.ensure_invite(s, user_id=uid, org_id=None)
    s.close()
    assert len(_invites(app_mod, uid)) == 1


def test_two_invitations_made_at_the_same_moment_become_one(tmp_path, monkeypatch):
    from anthill.web.notify import notify

    _unused_client, app_mod, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    for _ in range(2):  # what two pages loading together can do: both find none and both create one
        notify(
            s, user_id=uid, kind=community.KIND, title="t", link=community.INVITE_URL, push=False
        )
    community._keep_only_the_first(s, uid)
    s.close()
    assert len(_invites(app_mod, uid)) == 1


def test_a_user_from_before_the_release_day_gets_none(tmp_path, monkeypatch):
    client, app_mod, uid = _client(tmp_path, monkeypatch, created_at=BEFORE)
    client.get("/")
    client.get("/notifications")
    assert _invites(app_mod, uid) == []


def test_the_bell_counts_it_and_the_notification_page_clears_it(tmp_path, monkeypatch):
    client, _app, _uid = _client(tmp_path, monkeypatch)
    assert 'class="nav-badge">1<' in client.get("/").text.replace("\n", "")
    client.get("/notifications")
    assert 'href="/notifications"' in client.get("/").text
    assert 'nav-badge">1<' not in client.get("/").text.replace("\n", "")


def test_the_page_opens_the_invite_in_a_new_tab_safely_with_a_join_button(tmp_path, monkeypatch):
    client, _app, _uid = _client(tmp_path, monkeypatch)
    soup = BeautifulSoup(client.get("/notifications").text, "html.parser")
    link = soup.find("a", string="Join Slack")
    assert link["href"] == community.INVITE_URL
    assert link["target"] == "_blank" and link["rel"] == ["noopener", "noreferrer"]
    assert "Join the early-adopter Slack group" in soup.get_text()


def test_other_notifications_keep_their_in_app_link_and_open_button(tmp_path, monkeypatch):
    from anthill.web.notify import notify

    client, app_mod, uid = _client(tmp_path, monkeypatch, created_at=BEFORE)
    s = app_mod._SessionFactory()
    notify(s, user_id=uid, kind="review", title="A review", link="/wiki/review", push=False)
    notify(s, user_id=uid, kind="info", title="Elsewhere", link="https://example.org/x", push=False)
    s.close()
    soup = BeautifulSoup(client.get("/notifications").text, "html.parser")
    internal = soup.find("a", href="/wiki/review")
    assert internal.get_text() == "Open" and internal.get("target") is None
    external = soup.find("a", href="https://example.org/x")
    assert external.get_text() == "Open" and external["target"] == "_blank"
    assert soup.find("a", string="Join Slack") is None


def test_nothing_is_pushed_or_sent_outside_the_app(tmp_path, monkeypatch):
    import anthill.web.push as push_mod

    calls = []
    monkeypatch.setattr(push_mod, "send_to_user", lambda *a, **k: calls.append(a))
    client, _app, _uid = _client(tmp_path, monkeypatch)
    client.get("/")
    assert calls == []


def test_a_failure_to_write_the_invitation_never_costs_the_person_their_page(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the database is busy")

    monkeypatch.setattr(community, "notify", boom)
    client, app_mod, uid = _client(tmp_path, monkeypatch)
    assert client.get("/").status_code == 200
    assert _invites(app_mod, uid) == []


# ── on the dashboard, under "Needs your attention" ────────────────────────────────────────────


def _row(client):
    return BeautifulSoup(client.get("/").text, "html.parser").find(id="slack-invite")


def test_a_new_user_sees_the_invitation_on_their_first_dashboard_under_needs_your_attention(
    tmp_path, monkeypatch
):
    client, _app, _uid = _client(tmp_path, monkeypatch)
    soup = BeautifulSoup(client.get("/").text, "html.parser")  # the very first page they open
    row = soup.find(id="slack-invite")
    assert row is not None and row.find_parent(class_="card").find("h3").get_text() == (
        "Needs your attention"
    )
    assert "Join the early-adopter Slack group" in row.get_text()
    assert "steer the development of Anthill" in row.get_text()
    join = row.find("a", string="Join Slack")
    assert join["href"] == community.INVITE_URL
    assert join["target"] == "_blank" and join["rel"] == ["noopener", "noreferrer"]
    assert row.find("button", string="Not now") is not None


def test_a_user_from_before_the_release_day_sees_no_invitation_on_the_dashboard(
    tmp_path, monkeypatch
):
    client, _app, _uid = _client(tmp_path, monkeypatch, created_at=BEFORE)
    assert _row(client) is None


def test_not_now_hides_it_on_the_dashboard_and_clears_the_bell(tmp_path, monkeypatch):
    client, app_mod, uid = _client(tmp_path, monkeypatch)
    assert _row(client) is not None
    done = client.post("/community/slack/done", data={"next": "/"}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert _row(client) is None
    assert all(n.read for n in _invites(app_mod, uid)) and len(_invites(app_mod, uid)) == 1
    assert 'class="nav-badge">1<' not in client.get("/").text.replace("\n", "")


def test_clicking_join_slack_marks_it_done_without_leaving_the_page(tmp_path, monkeypatch):
    client, _app, _uid = _client(tmp_path, monkeypatch)
    assert "fetch('/community/slack/done'" in client.get("/").text  # what the click sends
    assert client.post("/community/slack/done").status_code == 204
    assert _row(client) is None


def test_opening_the_notifications_page_also_settles_it(tmp_path, monkeypatch):
    client, _app, _uid = _client(tmp_path, monkeypatch)
    assert _row(client) is not None
    client.get("/notifications")
    assert _row(client) is None


def test_the_done_route_needs_a_signed_in_person_and_touches_only_theirs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    client, app_mod, uid = _client(tmp_path, monkeypatch)
    client.get("/")
    anonymous = TestClient(app_mod.app)
    assert anonymous.post("/community/slack/done", follow_redirects=False).status_code in (
        302,
        303,
        401,
        403,
    )
    assert any(not n.read for n in _invites(app_mod, uid))  # still waiting for its owner
