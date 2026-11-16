"""A Solo ("just me") workspace never shows organisation, admin or teammate wording (#158), and its leaf
pages go back to the dashboard or Settings instead of "Manage organisation". An org account keeps its
existing wording. Model-free."""

import re
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod

# "organization" does not need its own entry: the pattern matches both spellings.
_ORG_WORDS = re.compile(r"\b(orgs?|organi[sz]ations?|admins?|administrators?|teammates?)\b", re.I)


class _VisibleText(HTMLParser):
    """Text a person can read in <main>: text nodes plus title and aria-label attributes."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0
        self._in_main = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "main":
            self._in_main = True
        if tag in ("script", "style"):
            self._skip += 1
        if self._in_main:
            self.parts += [a[k] for k in ("title", "aria-label") if a.get(k)]

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        if tag == "main":
            self._in_main = False

    def handle_data(self, data):
        if self._in_main and not self._skip:
            self.parts.append(data)


def _text(html: str) -> str:
    p = _VisibleText()
    p.feed(html)
    return " ".join(" ".join(p.parts).split())


def _client(tmp_path, monkeypatch, topology):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, Team, TeamMembership, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="Acme", slug="acme")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="admin", active=True)
    s.add(u)
    s.flush()
    s.add(OrgSettings(org_id=o.id, deployment_topology=topology))
    team = Team(org_id=o.id, name="Plan", slug="plan", owner_id=u.id)
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=u.id, role="owner", status="active"))
    s.commit()
    # the cloud-retry checkbox on /tasks only shows when a cloud endpoint is connected
    monkeypatch.setattr(app_mod, "_escalation_available", lambda db, org: True)
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, team.id


def _pages(team_id):
    return ["/agents", "/tasks", "/teams", f"/teams/{team_id}", "/metrics", "/audit", "/profile"]


@pytest.mark.parametrize("path_index", range(7))
def test_solo_pages_have_no_org_admin_or_teammate_wording(tmp_path, monkeypatch, path_index):
    c, team_id = _client(tmp_path, monkeypatch, "solo")
    path = _pages(team_id)[path_index]
    r = c.get(path)
    assert r.status_code == 200, path
    found = sorted({m.group(0).lower() for m in _ORG_WORDS.finditer(_text(r.text))})
    # the Solo project pages still point at setting an organisation up later: a deliberate pointer
    allowed = {"organisation"} if path.startswith("/teams") else set()
    assert set(found) - allowed == set(), (path, found)


def test_org_pages_keep_their_wording(tmp_path, monkeypatch):
    c, team_id = _client(tmp_path, monkeypatch, "org")
    pages = {p: _text(c.get(p).text) for p in _pages(team_id)}
    assert "org-wide defaults for new agents" in pages["/agents"]
    assert "Task settings for administrators" in pages["/tasks"]
    assert "limits that apply to every task in your organisation" in pages["/tasks"]
    assert "retry it once on your organisation's connected cloud model" in pages["/tasks"]
    assert "How your org is using Anthill" in pages["/metrics"]
    assert "sent to org admins" in pages["/audit"]
    assert "how you appear to teammates" in pages["/profile"]
    assert "role admin" in pages["/profile"]
    assert "promotions to the org are approved by the project owner" in pages[f"/teams/{team_id}"]
    assert "or the org wiki in an organisation" in pages["/teams"]


def test_solo_leaf_page_renders_no_manage_organisation_link(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup

    c, _ = _client(tmp_path, monkeypatch, "solo")
    for path, label, href in (
        ("/metrics", "Dashboard", "/"),
        ("/audit", "Settings", "/personalize"),
        ("/teams", "Dashboard", "/"),
    ):
        body = c.get(path).text
        assert "Manage organisation" not in body and "/settings/org" not in body.split("<main")[0]
        backs = [
            a for a in BeautifulSoup(body, "html.parser").find_all("a") if "\u2190" in a.get_text()
        ]
        assert [(a.get_text(" ", strip=True), a.get("href")) for a in backs] == [
            (f"\u2190 {label}", href)
        ], path
