"""Installable PWA / Chrome web app: the manifest is served and complete, and the standalone chat
page (which does not extend base.html) carries the manifest + service worker so it is installable on
Chrome / ChromeOS, not just the base-layout pages."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

import anthill.web.app as app_mod

_TEMPLATES = Path(app_mod.__file__).parent / "templates"


def test_manifest_is_served_and_complete():
    c = TestClient(app_mod.app)
    r = c.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    m = json.loads(r.content)
    # installable-app essentials
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["id"]
    assert any(i["sizes"] == "512x512" for i in m["icons"])
    assert any(
        i.get("purpose") == "maskable" for i in m["icons"]
    )  # adaptive icon on ChromeOS/Android
    # richer Chrome web app metadata
    assert m.get("categories") and m.get("shortcuts")
    assert {s["url"] for s in m["shortcuts"]} >= {"/chat", "/wiki"}


def test_service_worker_served_at_root_scope():
    # The SW must be served from / (root scope) so it controls the whole app, with the scope header.
    r = TestClient(app_mod.app).get("/sw.js")
    assert r.status_code == 200
    assert r.headers.get("service-worker-allowed") == "/"


def test_chat_page_is_installable():
    # /chat is the primary screen and has its own <head> (it does not extend base.html), so it must
    # link the manifest + register the service worker itself or it would not be installable there.
    html = (_TEMPLATES / "chat.html").read_text(encoding="utf-8")
    assert 'rel="manifest"' in html and "manifest.webmanifest" in html
    assert "serviceWorker" in html and "/sw.js" in html
    assert 'name="theme-color"' in html


def test_install_button_targets_the_pwa_off_macos():
    # On ChromeOS / non-Mac the install action installs the PWA directly (no native app exists there);
    # the macOS chooser path is reserved for Mac.
    html = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "CrOS" in html  # ChromeOS detection
    assert "browserInstall()" in html  # the direct PWA-install path
