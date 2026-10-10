"""Redirects stay on this site (docs/specs/local-only-redirects.md).

Four places send a member back to a page named in the request: the `next` field of the review and skill
forms, the password form's `next_url`, and the `Referer` of the pin and rename actions. Each accepts only a
plain local path. A backslash (browsers read `/\\host` as `//host`), control characters, a second leading
slash, a scheme or a host all fall back to the default. Model-free.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.web import db as db_mod
from anthill.web.app import _is_local_path, _local_or
from anthill.web.crypto import make_token
from anthill.web.db import Conversation, Organization, User

HOSTILE = [
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "/\\\\evil.example",
    "\\evil.example",
    "\\\\evil.example",
    "/\tevil.example",
    "/\nevil.example",
    "/\r\nSet-Cookie: x=1",
    "/\x00evil",
    "/\x7fevil",
    "http://evil.example",
    "https://evil.example/x",
    "javascript:alert(1)",
    "data:text/html,x",
    "evil.example",
    "",
    "x/y",
    "/ok\\path",
    "//",
    "/\\",
]
FINE = ["/", "/skills", "/wiki/org?scope=org&q=a%20b", "/chat/12#top", "/a/b/c", "/with space"]


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_target_is_not_a_local_path(value):
    assert _is_local_path(value) is False
    assert _local_or(value, "/fallback") == "/fallback"


@pytest.mark.parametrize("value", FINE)
def test_an_ordinary_local_path_is_kept(value):
    assert _is_local_path(value) is True
    assert _local_or(value, "/fallback") == value


@pytest.fixture
def world(tmp_path, monkeypatch):
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
    user = User(org_id=org.id, email="u@a.com", role="admin", active=True)
    s.add(user)
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, title="t")
    s.add(conv)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    yield client, conv.id
    s.close()


# ── the password form ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", HOSTILE)
def test_the_password_form_ignores_a_hostile_next_url(world, value):
    client, _ = world
    r = client.post(
        "/account/password",
        data={"new_password": "correcthorse1", "next_url": value},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].startswith("/account?")  # the default, nothing from the request


def test_the_password_form_still_honours_a_local_next_url(world):
    client, _ = world
    r = client.post(
        "/account/password",
        data={"new_password": "correcthorse1", "next_url": "/profile"},
        follow_redirects=False,
    )
    assert r.headers["location"].startswith("/profile?")


# ── the Referer of pin and rename ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "referer",
    [
        "https://anthill.example//evil.example",
        "https://anthill.example//evil.example/path?x=1",
        "https://anthill.example/\\evil.example",
        "evil.example",
        "",
    ],
)
def test_pin_and_rename_do_not_follow_a_hostile_referer(world, referer):
    client, conv_id = world
    headers = {"referer": referer} if referer else {}
    r = client.post(f"/chat/{conv_id}/pin", headers=headers, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location == "/chat"
    r = client.post(
        f"/chat/{conv_id}/rename", data={"title": "x"}, headers=headers, follow_redirects=False
    )
    location = r.headers["location"]
    assert location == "/chat"


def test_a_percent_encoded_backslash_is_just_a_local_path(world):
    """%5C stays text in a path that starts with a single slash, so it cannot leave the site."""
    client, conv_id = world
    r = client.post(
        f"/chat/{conv_id}/pin",
        headers={"referer": "https://anthill.example/%5Cevil.example/x"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/%5Cevil.example/x"


def test_pin_and_rename_return_to_the_page_they_came_from(world):
    client, conv_id = world
    r = client.post(
        f"/chat/{conv_id}/pin",
        headers={"referer": "https://anthill.example/chat/history?q=plan&page=2"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/chat/history?q=plan&page=2"
    r = client.post(
        f"/chat/{conv_id}/rename",
        data={"title": "renamed"},
        headers={"referer": "http://127.0.0.1:8000/chat"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/chat"


def test_the_task_page_referer_redirect_uses_the_same_rule(world):
    request = type(
        "R",
        (),
        {"headers": {"referer": "https://anthill.example//evil.example"}},
    )()
    response = app_mod._back_to_local(request, fallback="/tasks/5/result")
    assert response.headers["location"] == "/tasks/5/result"
