"""Every page before the app looks like one product (docs/specs/first-launch-one-look.md).

Setup, sign in, forgot password, choose a new password, accept an invitation and confirm your email share
one brand block (the hill and the name), one card width and one set of placeholders, and every field has a
label tied to it. Model-free: pages are rendered with a TestClient or straight from their templates.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    return TestClient(app_mod.app)


def _render(name, **ctx):
    return app_mod.templates.get_template(name).render({"request": None, **ctx})


def _pages(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    pages = {"setup": client.get("/setup").text}
    created = client.post(
        "/setup",
        data={"admin_email": "owner@example.com", "admin_password": "correct horse battery"},
        follow_redirects=False,
    )
    assert created.status_code == 302
    client.cookies.clear()
    pages["login"] = client.get("/login").text
    pages["forgot"] = client.get("/forgot").text
    pages["reset"] = _render("reset.html", email="a@b.example")
    pages["invite"] = _render("invite.html", email="a@b.example")
    pages["verify_sent"] = _render("verify_sent.html", email="a@b.example")
    return client, pages


def test_every_page_before_the_app_has_the_hill_and_the_name(tmp_path, monkeypatch):
    _, pages = _pages(tmp_path, monkeypatch)
    for name, html in pages.items():
        assert 'class="auth-brand"' in html, name
        assert 'class="auth-wordmark">Ant<span>hill</span>' in html, name
        assert html.count('class="auth-hill"') == 1, name


def test_every_field_has_a_label_tied_to_it(tmp_path, monkeypatch):
    _, pages = _pages(tmp_path, monkeypatch)
    for name, html in pages.items():
        fields = re.findall(r"<input\b[^>]*>", html)
        visible = [f for f in fields if not re.search(r'type="(hidden|submit)"', f)]
        if name == "verify_sent":
            continue  # only a hidden field and a button: nothing to label
        assert visible, name
        for field in visible:
            fid = re.search(r'\bid="([^"]+)"', field)
            assert fid, (name, field)
            assert f'<label for="{fid.group(1)}"' in html, (name, fid.group(1))


def test_the_pages_use_the_same_placeholders_and_copy(tmp_path, monkeypatch):
    _, pages = _pages(tmp_path, monkeypatch)
    for name, html in pages.items():
        assert "you@company.com" not in html, name
        assert "min 12 characters" not in html, name
        assert "Onehill" not in html, name
    assert 'placeholder="you@example.com"' in pages["login"]
    assert 'placeholder="you@example.com"' in pages["forgot"]
    assert 'placeholder="At least 12 characters"' in pages["setup"]
    assert 'placeholder="At least 12 characters"' in pages["reset"]
    assert 'placeholder="At least 12 characters"' in pages["invite"]
    assert "Your own private AI." in pages["login"]
    assert "Your organisation's private AI" not in pages["login"]


def test_the_card_is_one_width_for_every_page(tmp_path, monkeypatch):
    _, pages = _pages(tmp_path, monkeypatch)
    for name, html in pages.items():
        assert re.search(r'<div class="auth-box">', html), name  # no per-page width override
    css = Path("anthill/web/static/style.css").read_text()
    assert re.search(r"\.auth-box \{[^}]*max-width: 480px", css)


def test_the_sign_in_page_offers_sign_up_only_when_sign_up_is_open(tmp_path, monkeypatch):
    client, pages = _pages(tmp_path, monkeypatch)
    # open: the link is there, on the page and on a failed sign-in
    monkeypatch.setattr(app_mod, "signup_is_open", lambda db, request=None: True)
    pages["login"] = client.get("/login").text
    assert 'href="/setup"' in pages["login"] and "Create an account" in pages["login"]
    monkeypatch.setattr(app_mod, "signup_is_open", lambda db, request=None: False)
    closed = client.get("/login").text
    assert 'href="/setup"' not in closed and "Create an account" not in closed
    wrong = client.post("/login", data={"email": "owner@example.com", "password": "nope nope nope"})
    assert wrong.status_code == 401 and "Create an account" not in wrong.text


def test_the_desktop_splash_uses_the_app_font_without_the_network(tmp_path):
    splash = Path("src-tauri/splash/index.html").read_text()
    assert 'font-family: "Plus Jakarta Sans"' in splash
    assert (
        "data:font/woff2;base64," in splash
    )  # embedded: the backend is not up to serve /static yet
    assert "http://" not in splash.replace("http://127.0.0.1", "") and "https://" not in splash


def test_the_four_step_bar_shows_only_the_current_label_in_a_narrow_card():
    css = Path("anthill/web/templates/_setup_steps.html").read_text()
    assert "container-type:inline-size" in css
    assert ".wiz-step:not(.is-current) .wiz-label { display:none; }" in css
