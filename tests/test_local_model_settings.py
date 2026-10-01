"""The local model is a discoverable, list-based, fit-aware page (not a copy-paste tag buried under
Personalize): /models lists the four families annotated for this machine, picking one sets it as the
Solo model and pulls if needed in one step, and a sidebar item links to it."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def test_local_model_dropdown_excludes_models_too_big_for_this_machine(monkeypatch):
    # The Settings local-model dropdown offers only models that FIT this machine (same fit predicate the
    # /models picker uses), so it never lists a model too large to run here (the walkthrough complaint).
    import types

    import anthill.hosting.sizing as sizing
    import anthill.web.app as app_mod

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # a 16 GB machine
    monkeypatch.setattr(
        app_mod, "_installed_local_models", lambda cfg: []
    )  # don't probe real ollama
    cat = sizing.load_catalog()
    big = next((m.ollama_tag for m in cat if m.ollama_tag and m.params_b >= 70), None)
    small = next((m.ollama_tag for m in cat if m.ollama_tag and 0 < m.params_b <= 8), None)
    opts = app_mod._local_model_options(
        types.SimpleNamespace(ollama_url="http://x:11434", ollama_model="")
    )
    assert opts, "the dropdown should still offer fitting models"
    if big:
        assert big not in opts, f"{big} is too big for 16 GB and must be filtered out"
    if small:
        assert small in opts, f"{small} fits 16 GB and should be offered"


def _admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    # never spawn a real `ollama pull` from a test
    monkeypatch.setattr("anthill.inference.ollama.find_ollama_bin", lambda: None)
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add_all(
        [User(org_id=o.id, email="a@a.com", role="admin", active=True), OrgSettings(org_id=o.id)]
    )
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


def test_models_page_no_longer_duplicates_the_settings_catalog(tmp_path, monkeypatch):
    # The curated, hardware-ranked family picker used to live here too, duplicating Settings ->
    # Model -> "Change where it runs" (founder report, 2026-09-29: "clicking Manage opens up its own
    # model selection settings sub-setting screen, makes no sense"). /models now keeps only what
    # Settings can't do: pull an arbitrary tag, and manage what's already on disk.
    c, _, _ = _admin(tmp_path, monkeypatch)
    body = c.get("/models").text
    assert "Choose your local model" not in body
    assert 'type="radio"' not in body
    assert "Detected:" not in body
    assert 'name="model_tag"' in body  # the custom-tag pull form is still here
    assert "outside the curated catalog" in body
    # The raw on-disk "Installed on this device" storage list was removed (founder 2026-10-01): it
    # exposes model storage that doesn't belong in Anthill's UX. Uninstall now lives on each model in
    # the picker instead of a separate list.
    assert "Installed on this device" not in body


def test_solo_settings_has_no_separate_model_storage_link(tmp_path, monkeypatch):
    # The separate "Model storage" card and its "Manage" (/models#installed) link were removed
    # (founder 2026-10-01): uninstall now lives on each model in the picker, not a separate page.
    c, _, _ = _admin(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert 'href="/models#installed"' not in body  # the storage "Manage" link is gone
    assert 'href="/settings"' in body  # advanced inference / workspace link stays


def test_model_storage_card_removed_from_settings(tmp_path, monkeypatch):
    # The separate "Model storage" card was removed (founder 2026-10-01); uninstall is per-model in
    # the picker. Pin this so a future pass doesn't re-introduce a standalone raw-storage list.
    c, _, _ = _admin(tmp_path, monkeypatch)
    body = c.get("/personalize").text
    assert "Model storage" not in body  # no standalone storage card anywhere in Settings


def test_selecting_a_not_installed_model_downloads_without_switching_yet(tmp_path, monkeypatch):
    # The bug: it used to flip the active model the instant you clicked, so chatting mid-download had
    # nothing to serve. Now a not-installed model downloads in the background and the CURRENT model
    # keeps serving until it finishes.
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first().ollama_model = "qwen2.5:3b"
    s.commit()  # the current, working model
    started = {}
    monkeypatch.setattr(
        app_mod, "_start_model_pull", lambda oid, tag, **k: started.update(o=oid, t=tag)
    )
    r = c.post("/models/pull", data={"model_tag": "llama3.1:8b"}, follow_redirects=False)
    assert "pulling=llama3.1:8b" in r.headers["location"]
    assert started == {"o": org_id, "t": "llama3.1:8b"}  # background download kicked off
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.ollama_model == "qwen2.5:3b"  # NOT switched yet - current model still serves


def test_selecting_an_installed_model_switches_now(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.installed_models", lambda self: ["gemma2:9b"]
    )
    r = c.post("/models/pull", data={"model_tag": "gemma2:9b"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.ollama_model == "gemma2:9b"  # already installed -> switch immediately
    assert "selected=1" in r.headers["location"]


def test_pull_completion_activates_the_model_and_clears_the_flag(tmp_path, monkeypatch):
    # The background thread calls _set_model_pulling on completion; test that contract directly.
    _c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    app_mod._set_model_pulling(org_id, "llama3.1:8b")  # download in progress
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.local_model_pulling == "llama3.1:8b" and cfg.ollama_model != "llama3.1:8b"
    app_mod._set_model_pulling(org_id, "", activate="llama3.1:8b")  # download finished OK
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    assert cfg.local_model_pulling == "" and cfg.ollama_model == "llama3.1:8b"  # now switched


def test_pull_status_endpoint_reports_progress(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    app_mod._set_model_pulling(org_id, "mistral-nemo:12b")
    j = c.get("/models/pull-status").json()
    assert j["pulling"] == "mistral-nemo:12b"  # the page poller sees the live download


def test_custom_tag_still_works(tmp_path, monkeypatch):
    c, app_mod, _org_id = _admin(tmp_path, monkeypatch)
    started = {}
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: started.update(t=tag))
    r = c.post(
        "/models/pull", data={"model_tag": "hf.co/bartowski/Some-GGUF"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert started["t"] == "hf.co/bartowski/Some-GGUF"  # power-user free-text still downloads


def test_empty_tag_is_rejected(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    r = c.post("/models/pull", data={"model_tag": "  "}, follow_redirects=False)
    assert "error=no_tag" in r.headers["location"]


# ── uninstall a downloaded model (issue #415) ──────────────────────────────────


def _set_current(app_mod, org_id, tag):
    s = app_mod._SessionFactory()
    s.query(OrgSettings).filter(OrgSettings.org_id == org_id).first().ollama_model = tag
    s.commit()


def test_delete_removes_a_non_current_model(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _set_current(app_mod, org_id, "qwen2.5:3b")  # serving this one
    deleted = {}
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.delete_model",
        lambda self, tag: deleted.update(tag=tag) or True,
    )
    r = c.post("/models/delete", data={"model_tag": "llama3.1:8b"}, follow_redirects=False)
    assert deleted == {"tag": "llama3.1:8b"}  # the uninstall actually ran
    assert "deleted=llama3.1:8b" in r.headers["location"]


def test_delete_refuses_the_current_model(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _set_current(app_mod, org_id, "qwen2.5:3b")
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.delete_model",
        lambda self, tag: pytest.fail("must never delete the serving model"),
    )
    r = c.post("/models/delete", data={"model_tag": "qwen2.5:3b"}, follow_redirects=False)
    assert "error=in_use" in r.headers["location"]  # switch away first


def test_delete_refuses_the_downloading_model(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    app_mod._set_model_pulling(org_id, "gemma2:9b")  # mid-download
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.delete_model",
        lambda self, tag: pytest.fail("must not delete a model that's still downloading"),
    )
    r = c.post("/models/delete", data={"model_tag": "gemma2:9b"}, follow_redirects=False)
    assert "error=in_use" in r.headers["location"]


def test_delete_failure_is_surfaced(tmp_path, monkeypatch):
    c, app_mod, org_id = _admin(tmp_path, monkeypatch)
    _set_current(app_mod, org_id, "qwen2.5:3b")
    monkeypatch.setattr(
        "anthill.inference.ollama.OllamaBackend.delete_model", lambda self, tag: False
    )
    r = c.post("/models/delete", data={"model_tag": "llama3.1:8b"}, follow_redirects=False)
    assert "error=delete_failed" in r.headers["location"]


def test_backend_delete_and_resident(monkeypatch):
    import anthill.inference.ollama as om

    calls = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "qwen3:8b"}]}

    monkeypatch.setattr(
        om.httpx,
        "request",
        lambda method, url, **k: calls.update(method=method, json=k.get("json")) or _Resp(),
    )
    monkeypatch.setattr(om.httpx, "get", lambda url, **k: _Resp())
    be = om.OllamaBackend("http://x", "qwen3:8b")
    assert be.delete_model("gemma2:9b") is True
    assert calls == {"method": "DELETE", "json": {"name": "gemma2:9b"}}  # ollama rm, right payload
    assert be.resident_models() == {"qwen3:8b"}  # from /api/ps


# --- refresh-catalog: the network is untrusted -------------------------------------------------------
#
# The route now demands a Sigstore signature over the exact bytes served. A real Fulcio certificate cannot
# be minted in a unit test (it needs an OIDC identity), so the signature CHECK itself is stubbed here and
# covered on its own in tests/test_catalog_trust.py; what these assert is the route's policy around it.


class _Resp:
    is_redirect = False
    status_code = 200

    def __init__(self, payload, content=b"{}"):
        self._payload = payload
        self.content = content

    @property
    def text(self):
        return self.content.decode()

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _override_path(tmp_path):
    from pathlib import Path

    return Path(tmp_path) / "model_catalog.json"


def _serve(monkeypatch, catalog, *, signed=True, bundle_status=200):
    """Serve a catalog at the pinned URL and a bundle beside it, as the real host would."""
    import json as _json

    import httpx

    import anthill.hosting.catalog_trust as ct

    def fake_get(url, **kw):
        if url.endswith(".sigstore.json"):
            r = _Resp({}, content=b'{"pretend":"bundle"}')
            r.status_code = bundle_status
            return r
        return _Resp(catalog, content=_json.dumps(catalog).encode())

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(
        ct, "verify_catalog", lambda payload, bundle: "" if signed else "signed by someone else"
    )


def test_refresh_refuses_a_catalog_that_redirects_the_pull(tmp_path, monkeypatch):
    """A tampered catalog must not be able to hand this box someone else's weights."""
    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    _serve(
        monkeypatch,
        {
            "generated": "2026-07-17",
            "models": [
                {"name": "Real", "ollama_tag": "qwen3.6", "hf_id": "Qwen/Qwen3.6-35B-A3B"},
                {"name": "Pwn", "ollama_tag": "hf.co/attacker/evil-GGUF", "intelligence": 99},
            ],
        },
    )
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "unexpected" in body["error"]
    assert not _override_path(tmp_path).exists()  # fail closed: nothing written


def test_refresh_refuses_a_catalog_signed_by_the_wrong_publisher(tmp_path, monkeypatch):
    """Owning the host/CDN/DNS is not enough: it must be signed by OUR workflow identity."""
    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    _serve(
        monkeypatch,
        {"generated": "2026-07-17", "models": [{"name": "Real", "ollama_tag": "qwen3.6"}]},
        signed=False,
    )
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "expected publisher" in body["error"]
    assert not _override_path(tmp_path).exists()


def test_refresh_refuses_an_unsigned_catalog(tmp_path, monkeypatch):
    """No bundle beside the catalog -> refuse, rather than fall back to trusting the host."""
    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    _serve(
        monkeypatch,
        {"generated": "2026-07-17", "models": [{"name": "Real", "ollama_tag": "qwen3.6"}]},
        bundle_status=404,
    )
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "not signed" in body["error"]
    assert not _override_path(tmp_path).exists()


def test_refresh_refuses_a_validly_signed_but_older_catalog(tmp_path, monkeypatch):
    """Rollback: a correct signature over LAST week's catalog would re-introduce a dropped model."""
    import json as _json

    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    _override_path(tmp_path).write_text(
        _json.dumps(
            {"generated": "2026-07-17", "models": [{"name": "Cur", "ollama_tag": "cur:7b"}]}
        )
    )
    _serve(
        monkeypatch,
        {"generated": "2026-01-01", "models": [{"name": "Old", "ollama_tag": "old:7b"}]},
    )
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "older" in body["error"]
    # the catalog we already trust is untouched
    assert _json.loads(_override_path(tmp_path).read_text())["generated"] == "2026-07-17"


def test_refresh_refuses_a_redirect_rather_than_following_it(tmp_path, monkeypatch):
    import httpx

    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))

    class _Redirect(_Resp):
        is_redirect = True

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Redirect({}))
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "redirect" in body["error"].lower()
    assert not _override_path(tmp_path).exists()


def test_refresh_requires_https(tmp_path, monkeypatch):
    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_MODEL_CATALOG_URL", "http://anthill.run/model-catalog.json")
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "https" in body["error"]


def test_refresh_accepts_a_clean_signed_catalog_and_stores_only_validated_fields(
    tmp_path, monkeypatch
):
    import json as _json

    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    _serve(
        monkeypatch,
        {
            "note": "n",
            "generated": "2026-07-17",
            "evil_extra": {"x": 1},  # an unvetted key from the network
            "models": [{"name": "Real", "ollama_tag": "qwen3.6", "hf_id": "Qwen/Qwen3.6-35B-A3B"}],
        },
    )
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is True and body["count"] == 1
    saved = _json.loads(_override_path(tmp_path).read_text())
    assert [m["name"] for m in saved["models"]] == ["Real"]
    assert "evil_extra" not in saved  # only the normalised, validated document is persisted


def test_refresh_rejects_an_oversized_payload(tmp_path, monkeypatch):
    import httpx

    c, _, _ = _admin(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    big = _Resp({"models": []}, content=b"x" * 2_000_000)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: big)
    body = c.post("/models/refresh-catalog").json()
    assert body["ok"] is False and "too much data" in body["error"]
