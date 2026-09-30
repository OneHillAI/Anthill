"""Profiles (RFC-0003, P0): the registry, per-profile data-dir resolution, zero-copy migration,
and the desktop env-rooting that gives each profile an isolated data home."""

import os
import sqlite3

import pytest

from anthill import profiles

# Every persistent-state var the launcher roots at a profile's data dir. Tests clear these so a
# stray value from the real environment can't mask a rooting bug.
_ENV_KEYS = (
    "ANTHILL_PROFILE",
    "ANTHILL_HOME",
    "ANTHILL_DB",
    "ANTHILL_WORKSPACE",
    "ANTHILL_FILES_DIR",
    "ANTHILL_SKILLS_DIR",
    "ANTHILL_WIKI_ROOT",
    "ANTHILL_ORG_WIKI",
)


def _clean_env(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _seed_legacy_db(base, org_name):
    """Write a minimal legacy anthill.db with one organizations row, as an existing install has."""
    base.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(base / "anthill.db")
    con.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO organizations (name) VALUES (?)", (org_name,))
    con.commit()
    con.close()


# --- migration -------------------------------------------------------------------------------


def test_migrate_fresh_is_personal(tmp_path):
    reg = profiles.migrate_or_init(tmp_path)
    p = reg["profiles"][0]
    assert p["id"] == "default"
    assert p["name"] == "Personal"
    assert p["data_dir"] == "."  # zero copy: the default profile *is* the base dir
    assert profiles.data_dir_of(tmp_path, p) == tmp_path


def test_migrate_names_default_from_existing_org(tmp_path):
    _seed_legacy_db(tmp_path, "Acme Corp")
    reg = profiles.migrate_or_init(tmp_path)
    p = reg["profiles"][0]
    assert p["id"] == "default"
    assert p["name"] == "Acme Corp"  # named after the user's current identity
    assert profiles.data_dir_of(tmp_path, p) == tmp_path  # still points at the data in place


def test_migrate_is_idempotent(tmp_path):
    first = profiles.migrate_or_init(tmp_path)
    profiles.create_profile(tmp_path, "Work")
    second = profiles.migrate_or_init(tmp_path)
    # A second migrate loads what exists, it does not reset or duplicate the default.
    assert first["profiles"][0]["created_at"] == second["profiles"][0]["created_at"]
    assert len(second["profiles"]) == 2


def test_corrupt_registry_reinitialises(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    profiles.registry_path(tmp_path).write_text("{ not valid json")
    assert profiles.load_registry(tmp_path) is None  # never raises on a broken file
    reg = profiles.migrate_or_init(tmp_path)  # rebuilds cleanly
    assert reg["profiles"][0]["id"] == "default"


# --- creation + isolation --------------------------------------------------------------------


def test_create_profile_is_isolated(tmp_path):
    prof, over_cap = profiles.create_profile(tmp_path, "Work")
    assert over_cap is False
    assert prof["id"] == "work"
    work_dir = profiles.data_dir_of(tmp_path, prof)
    assert work_dir == tmp_path / "profiles" / "work"
    assert work_dir.is_dir()

    default_dir = profiles.data_dir_of(tmp_path, profiles.get_profile(tmp_path, "default"))
    assert default_dir == tmp_path
    # The hard boundary: distinct data homes => distinct databases.
    assert work_dir != default_dir
    assert (work_dir / "anthill.db") != (default_dir / "anthill.db")


def test_create_assigns_distinct_colours(tmp_path):
    a, _ = profiles.create_profile(tmp_path, "A")
    b, _ = profiles.create_profile(tmp_path, "B")
    default = profiles.get_profile(tmp_path, "default")
    assert len({default["colour"], a["colour"], b["colour"]}) == 3


def test_duplicate_name_rejected(tmp_path):
    profiles.create_profile(tmp_path, "Work")
    with pytest.raises(ValueError):
        profiles.create_profile(tmp_path, "work")  # case-insensitive duplicate
    with pytest.raises(ValueError):
        profiles.create_profile(tmp_path, "   ")  # blank


def test_slug_collision_gets_suffix(tmp_path):
    a, _ = profiles.create_profile(tmp_path, "Work Laptop")
    b, _ = profiles.create_profile(tmp_path, "Work.Laptop")  # distinct name, same slug
    assert a["id"] == "work-laptop"
    assert b["id"] == "work-laptop-2"


def test_soft_cap_flags_but_allows(tmp_path):
    # default counts as one; SOFT_CAP is 3, so the profile that makes it four trips the flag.
    flags = [profiles.create_profile(tmp_path, n)[1] for n in ("A", "B", "C")]
    assert flags == [False, False, True]
    assert len(profiles.list_profiles(tmp_path)) == 4  # over the cap, but still created


# --- resolution ------------------------------------------------------------------------------


def test_get_profile_by_name_and_id(tmp_path):
    profiles.create_profile(tmp_path, "Work Laptop")
    assert profiles.get_profile(tmp_path, "work-laptop")["name"] == "Work Laptop"
    assert profiles.get_profile(tmp_path, "Work Laptop")["id"] == "work-laptop"
    assert profiles.get_profile(tmp_path, "missing") is None


def test_resolve_precedence(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    profiles.create_profile(tmp_path, "Work")

    # explicit name beats everything
    assert profiles.resolve(tmp_path, "work")["id"] == "work"
    # $ANTHILL_PROFILE is next
    monkeypatch.setenv("ANTHILL_PROFILE", "work")
    assert profiles.resolve(tmp_path)["id"] == "work"
    # then the registry's remembered active (set by the resolves above)
    monkeypatch.delenv("ANTHILL_PROFILE", raising=False)
    assert profiles.resolve(tmp_path)["id"] == "work"
    # an unknown name is fail-safe: falls back to default, never raises
    assert profiles.resolve(tmp_path, "nope")["id"] == "default"


def test_resolve_updates_last_used_and_active(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    profiles.create_profile(tmp_path, "Work")
    profiles.resolve(tmp_path, "work")
    reg = profiles.load_registry(tmp_path)
    assert reg["active"] == "work"


def test_active_data_dir_creates_dir(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    profiles.create_profile(tmp_path, "Work")
    d = profiles.active_data_dir(tmp_path, "work")
    assert d == tmp_path / "profiles" / "work"
    assert d.is_dir()


def test_registry_persists_to_disk(tmp_path):
    profiles.create_profile(tmp_path, "Work")
    reloaded = profiles.load_registry(tmp_path)
    assert reloaded is not None
    assert {p["id"] for p in reloaded["profiles"]} == {"default", "work"}


# --- desktop env rooting ---------------------------------------------------------------------


def test_configure_env_default_is_base(tmp_path, monkeypatch):
    import anthill.desktop as desktop

    _clean_env(monkeypatch)
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_seed_skills", lambda: None)

    d = desktop.configure_env()
    assert d == tmp_path  # the default profile is byte-identical to the pre-profiles data dir
    assert os.environ["ANTHILL_DB"] == str(tmp_path / "anthill.db")


def test_configure_env_respects_profile_env(tmp_path, monkeypatch):
    import anthill.desktop as desktop

    _clean_env(monkeypatch)
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_seed_skills", lambda: None)
    profiles.create_profile(tmp_path, "Work")
    monkeypatch.setenv("ANTHILL_PROFILE", "work")

    d = desktop.configure_env()
    work = tmp_path / "profiles" / "work"
    assert d == work
    assert os.environ["ANTHILL_DB"] == str(work / "anthill.db")


def test_activate_profile_forces_over_ambient_env(tmp_path, monkeypatch):
    import anthill.desktop as desktop

    _clean_env(monkeypatch)
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_seed_skills", lambda: None)
    profiles.create_profile(tmp_path, "Work")
    # An ambient ANTHILL_DB (e.g. a stale .env) must not win over an explicit --profile.
    monkeypatch.setenv("ANTHILL_DB", "/somewhere/else.db")

    d = desktop.activate_profile("work")
    work = tmp_path / "profiles" / "work"
    assert d == work
    assert os.environ["ANTHILL_DB"] == str(work / "anthill.db")
    assert os.environ["ANTHILL_HOME"] == str(work)
