"""Profiles in the app (RFC-0003 P1a): a signed-in user sees every isolated profile on the device
and can create one; the running profile is badged Active. Device-level, not org-role-gated."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill import profiles
from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    base = tmp_path / "appdata"
    base.mkdir()
    # The registry lives at the base dir; ANTHILL_HOME is this backend's active profile (the default
    # profile's dir == base), so the default row renders as Active.
    monkeypatch.setenv("ANTHILL_PROFILES_BASE", str(base))
    monkeypatch.setenv("ANTHILL_HOME", str(base))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    try:
        org = Organization(name="Acme", slug="acme")
        s.add(org)
        s.flush()
        # A plain member, not an admin: the page must not be admin-gated.
        user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
        s.add(user)
        s.commit()
        uid, oid = user.id, org.id
    finally:
        s.close()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(uid, oid, "member"))
    return client, base


def test_page_lists_default_as_active(tmp_path, monkeypatch):
    client, _base = _app(tmp_path, monkeypatch)
    r = client.get("/profiles")
    assert r.status_code == 200
    assert "Personal" in r.text  # fresh migration names the default profile "Personal"
    assert "Active" in r.text


def test_create_profile_appears_and_is_isolated(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    r = client.post("/profiles", data={"name": "Work", "colour": "#059669"}, follow_redirects=False)
    assert r.status_code == 303
    prof = profiles.get_profile(base, "work")
    assert prof is not None
    assert profiles.data_dir_of(base, prof) == base / "profiles" / "work"  # own subdir

    page = client.get("/profiles").text
    assert "Work" in page
    # A non-active profile shows how to open it (the switch/open UI lands with the desktop shell).
    assert "anthill web --profile work" in page


def test_duplicate_name_shows_error(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    r = client.post("/profiles", data={"name": "work"}, follow_redirects=True)  # case-insensitive
    assert "already exists" in r.text
    assert len(profiles.list_profiles(base)) == 2  # default + Work only


def test_blank_name_rejected(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    r = client.post("/profiles", data={"name": "   "}, follow_redirects=True)
    assert r.status_code == 200
    assert len(profiles.list_profiles(base)) == 1  # only the default


def test_requires_login(tmp_path, monkeypatch):
    client, _base = _app(tmp_path, monkeypatch)
    client.cookies.clear()
    r = client.get("/profiles", follow_redirects=False)
    assert r.status_code != 200  # redirected to /login (303)


# --- management: rename / recolour / delete --------------------------------------------------


def test_rename_keeps_id_and_data(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    before = profiles.get_profile(base, "work")
    r = client.post(
        "/profiles/work/edit",
        data={"name": "Job", "colour": before["colour"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = profiles.get_profile(base, "work")  # same id
    assert after["name"] == "Job"
    assert after["data_dir"] == before["data_dir"]  # data did not move


def test_recolour(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work", "colour": "#059669"}, follow_redirects=False)
    client.post(
        "/profiles/work/edit", data={"name": "Work", "colour": "#DB2777"}, follow_redirects=False
    )
    assert profiles.get_profile(base, "work")["colour"] == "#DB2777"


def test_rename_to_another_name_rejected(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    # renaming the default profile to "Work" collides with the existing one
    r = client.post(
        "/profiles/default/edit", data={"name": "Work", "colour": "#4F46E5"}, follow_redirects=True
    )
    assert "already exists" in r.text
    assert profiles.get_profile(base, "default")["name"] == "Personal"  # unchanged


def test_delete_removes_profile_and_data(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Temp"}, follow_redirects=False)
    d = profiles.data_dir_of(base, profiles.get_profile(base, "temp"))
    assert d.is_dir()
    r = client.post("/profiles/temp/delete", follow_redirects=False)
    assert r.status_code == 303
    assert profiles.get_profile(base, "temp") is None  # gone from the registry
    assert not d.exists()  # and its data directory removed


def test_cannot_delete_default(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles/default/delete", follow_redirects=True)
    assert profiles.get_profile(base, "default") is not None  # refused


def test_cannot_delete_current_profile(tmp_path, monkeypatch):
    client, base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    # pretend this backend is running as "work" (its data dir is $ANTHILL_HOME)
    monkeypatch.setenv(
        "ANTHILL_HOME", str(profiles.data_dir_of(base, profiles.get_profile(base, "work")))
    )
    client.post("/profiles/work/delete", follow_redirects=True)
    assert profiles.get_profile(base, "work") is not None  # refused: can't delete the live one


def test_delete_button_hidden_for_default_and_current(tmp_path, monkeypatch):
    client, _base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    page = client.get("/profiles").text
    # default is current here (ANTHILL_HOME == base), so no delete form targets default...
    assert "/profiles/default/delete" not in page
    # ...but the non-current "work" profile does get a delete control.
    assert "/profiles/work/delete" in page


def test_open_trigger_renders_for_non_current(tmp_path, monkeypatch):
    client, _base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    page = client.get("/profiles").text
    # A non-current profile gets an Open button wired to the shell's open_profile command...
    assert 'class="btn btn-outline btn-sm profile-open" data-id="work"' in page
    assert 'invoke("open_profile"' in page
    # ...while the current (default) profile gets no Open button.
    assert 'data-id="default"' not in page


def test_delete_uses_two_click_not_native_confirm(tmp_path, monkeypatch):
    # The desktop app's webview doesn't reliably run window.confirm(), which silently cancelled the
    # delete form. The control is now a two-click confirm (a type=button that JS submits), not an
    # onsubmit=confirm gate - so the delete actually reaches the (working) route.
    client, _base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    page = client.get("/profiles").text
    assert 'class="profile-del"' in page and "profile-del-btn" in page
    assert "return confirm(" not in page  # the fragile native confirm is gone
    assert "Click again to delete" in page  # the two-click arming label


def test_open_errors_surface_in_page_not_alert(tmp_path, monkeypatch):
    # Open failures are shown in the page (the webview's alert() is unreliable), so a failed switch is
    # visible instead of looking like "nothing happened".
    client, _base = _app(tmp_path, monkeypatch)
    client.post("/profiles", data={"name": "Work"}, follow_redirects=False)
    page = client.get("/profiles").text
    assert "profile-js-error" in page and "showError" in page
    assert 'alert("Could not open' not in page  # no more swallowed alert
