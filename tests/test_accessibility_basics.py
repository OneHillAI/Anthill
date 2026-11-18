"""Accessibility basics every page keeps (docs/specs/accessibility-basics.md).

A keyboard user can skip the menu, a screen reader finds the main region, and the chat page's fields say what
they are for. Model-free: pages are rendered with a TestClient.
"""

import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Conversation, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setattr("anthill.cache.embedder.safe_embed", lambda text: None)
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
    user = User(org_id=org.id, email="u@acme.com", role="admin", active=True)
    s.add(user)
    s.flush()
    conv = Conversation(org_id=org.id, user_id=user.id, plane="solo")
    s.add(conv)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, conv.id


def _pages(tmp_path, monkeypatch):
    client, cid = _app(tmp_path, monkeypatch)
    return {
        "chat": client.get(f"/chat/{cid}").text,
        "tasks": client.get("/tasks").text,
        "dashboard": client.get("/").text,
    }


def test_every_page_starts_with_a_skip_link_to_its_main_region(tmp_path, monkeypatch):
    for name, html in _pages(tmp_path, monkeypatch).items():
        body = html[html.index("<body") :]
        first_link = re.search(r"<a\b[^>]*>", body).group(0)
        assert 'class="skip-link"' in first_link and 'href="#main-content"' in first_link, name
        assert html.count('id="main-content"') == 1, name  # the target exists, once
        target = re.search(r"<[a-z]+\b[^>]*\bid=\"main-content\"[^>]*>", html).group(0)
        assert target.startswith("<main") or 'role="main"' in target, name


def test_the_skip_link_is_hidden_until_it_has_focus():
    css = Path("anthill/web/static/style.css").read_text()
    assert re.search(r"\.skip-link \{[^}]*top: -64px", css)
    assert re.search(r"\.skip-link:focus \{[^}]*top: 12px", css)


def test_the_chat_fields_say_what_they_are_for(tmp_path, monkeypatch):
    chat = _pages(tmp_path, monkeypatch)["chat"]
    for field_id, label in (
        ("msg-input", "Message"),
        ("image-file", "Attach an image"),
        ("snip-tags", "Snippet tags, separated by commas"),
        ("canvas-req", "Request a change"),
    ):
        tag = re.search(rf"<[a-z]+\b[^>]*\bid=\"{field_id}\"[^>]*>", chat).group(0)
        assert f'aria-label="{label}"' in tag, field_id
    assert 'class="sched-when" aria-label="When to run"' in chat


def test_no_sidebar_label_is_smaller_than_twelve_pixels():
    css = Path("anthill/web/static/style.css").read_text()
    for selector in (".rail-conv-section {", ".profile-menu-label {", ".profile-menu-badge {"):
        rule = css[css.index(selector) : css.index("}", css.index(selector))]
        size = int(re.search(r"font-size: (\d+)px", rule).group(1))
        assert size >= 12, selector
