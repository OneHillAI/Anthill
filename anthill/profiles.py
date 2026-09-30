"""Profiles: multiple isolated accounts on one Anthill install (RFC-0003, phase P0).

A profile is a named, self-contained data home - its own database, workspace, wikis, fine-tunes
and secrets. Profiles are a hard boundary (separate directories, separate encryption keys) and they
sit *above* the solo/team/org planes: individual and org conversations still live inside a profile,
profiles do not replace planes. This module owns the on-disk registry and resolves which profile is
active. It deliberately does not import the app, the planes, or the model runtime - it is stdlib
only so it can run at the very start of a launch, before anything reads ANTHILL_DB.

P0 is backend only: the registry, per-profile data-dir resolution, and a zero-copy migration that
turns an existing single-user install into a "default" profile pointing at the data in place. The
desktop launcher (P1) and the in-app switcher (P2+) build on this.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_NAME = "profiles.json"
DEFAULT_ID = "default"
# RFC-0003 decision 4: a nudge, not a hard cap. The architecture is N-agnostic (a profile is a data
# dir + a free-port backend); only how many are *open at once* costs anything. Creation past this is
# allowed - callers just surface a gentle notice.
SOFT_CAP = 3

# A small, stable palette so each profile gets a distinct accent in the switcher (P2). Indigo first,
# so an unchanged single-user install keeps a sensible default colour.
_PALETTE = ["#4F46E5", "#059669", "#DB2777", "#D97706", "#0891B2", "#7C3AED"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "profile"


def registry_path(base: Path) -> Path:
    return base / REGISTRY_NAME


def load_registry(base: Path) -> dict | None:
    """Read profiles.json, or None if it is missing/unreadable/malformed. Never raises: a corrupt
    registry must not brick a launch - callers treat None as 'not migrated yet'."""
    p = registry_path(base)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("profiles"), list)
        or not data["profiles"]
    ):
        return None
    return data


def save_registry(base: Path, reg: dict) -> None:
    base.mkdir(parents=True, exist_ok=True)
    registry_path(base).write_text(json.dumps(reg, indent=2) + "\n")


def _legacy_org_name(base: Path) -> str | None:
    """Best-effort read of the existing install's org name, to name the migrated profile after the
    user's current identity. Never raises: a fresh, missing or unreadable DB just yields None."""
    db = base / "anthill.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT name FROM organizations ORDER BY id LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row and isinstance(row[0], str) and row[0].strip():
        return row[0].strip()
    return None


def _make(id_: str, name: str, colour: str, rel_dir: str) -> dict:
    now = _now()
    return {
        "id": id_,
        "name": name,
        "colour": colour,
        # Relative to base so the registry survives the app dir being relocated. "." = the base
        # itself (the default profile, migrated zero-copy).
        "data_dir": rel_dir,
        "created_at": now,
        "last_used": now,
    }


def migrate_or_init(base: Path) -> dict:
    """Return the registry, creating it on first run. An existing install becomes the "default"
    profile pointing at its data *in place* (zero copy), named after its org (else "Personal").
    Idempotent - a second call just loads what the first wrote."""
    reg = load_registry(base)
    if reg is not None:
        return reg
    name = _legacy_org_name(base) or "Personal"
    reg = {
        "version": 1,
        "active": DEFAULT_ID,
        "profiles": [_make(DEFAULT_ID, name, _PALETTE[0], ".")],
    }
    save_registry(base, reg)
    return reg


def list_profiles(base: Path) -> list[dict]:
    return list(migrate_or_init(base)["profiles"])


def get_profile(base: Path, name_or_id: str) -> dict | None:
    key = name_or_id.strip().lower()
    for p in migrate_or_init(base)["profiles"]:
        if p["id"] == key or p["name"].lower() == key:
            return p
    return None


def data_dir_of(base: Path, profile: dict) -> Path:
    rel = profile.get("data_dir", ".")
    return base if rel in (".", "") else base / rel


def create_profile(base: Path, name: str, colour: str | None = None) -> tuple[dict, bool]:
    """Create an isolated profile under base/profiles/<slug>. Returns (profile, over_soft_cap).
    Raises ValueError on a blank or duplicate name."""
    name = name.strip()
    if not name:
        raise ValueError("a profile needs a name")
    reg = migrate_or_init(base)
    if get_profile(base, name) is not None:
        raise ValueError(f"a profile named {name!r} already exists")
    existing_ids = {p["id"] for p in reg["profiles"]}
    pid = _slug(name)
    if pid in existing_ids:  # slug collides with a differently-cased/spaced name
        n = 2
        while f"{pid}-{n}" in existing_ids:
            n += 1
        pid = f"{pid}-{n}"
    if colour is None:
        used = {p.get("colour") for p in reg["profiles"]}
        colour = next(
            (c for c in _PALETTE if c not in used),
            _PALETTE[len(reg["profiles"]) % len(_PALETTE)],
        )
    rel = f"profiles/{pid}"
    (base / rel).mkdir(parents=True, exist_ok=True)
    prof = _make(pid, name, colour, rel)
    reg["profiles"].append(prof)
    save_registry(base, reg)
    return prof, len(reg["profiles"]) > SOFT_CAP


def _touch(base: Path, reg: dict, profile: dict) -> None:
    profile["last_used"] = _now()
    reg["active"] = profile["id"]
    save_registry(base, reg)


def resolve(base: Path, name: str | None = None) -> dict:
    """Pick the active profile - precedence: explicit name > $ANTHILL_PROFILE > registry 'active' >
    default. Fail-safe: an unknown name falls back to default rather than bricking a packaged launch
    (the launcher validates names up front; this is the last line of defence)."""
    reg = migrate_or_init(base)
    want = name or os.environ.get("ANTHILL_PROFILE") or reg.get("active") or DEFAULT_ID
    key = str(want).strip().lower()
    chosen = next((p for p in reg["profiles"] if p["id"] == key or p["name"].lower() == key), None)
    if chosen is None:
        chosen = next((p for p in reg["profiles"] if p["id"] == DEFAULT_ID), reg["profiles"][0])
    _touch(base, reg, chosen)
    return chosen


def active_data_dir(base: Path, name: str | None = None) -> Path:
    """The data home for the active profile, created if missing. This is the single function the
    launcher/env-setup calls; everything downstream (DB, workspace, wikis, secrets) roots at it."""
    d = data_dir_of(base, resolve(base, name))
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_id(base: Path) -> str:
    """The id of the profile this process is running as: the one whose data dir is $ANTHILL_HOME.
    Falls back to the registry's remembered active, then default, when the env is unset."""
    reg = migrate_or_init(base)
    here = os.environ.get("ANTHILL_HOME", "")
    if here:
        for p in reg["profiles"]:
            if str(data_dir_of(base, p)) == here:
                return p["id"]
    return reg.get("active") or DEFAULT_ID


def update_profile(
    base: Path, id_: str, name: str | None = None, colour: str | None = None
) -> dict:
    """Rename and/or recolour a profile in place - the id and data dir never change, so no data moves.
    Raises ValueError on a missing profile, a blank name, or a name already used by another profile."""
    reg = migrate_or_init(base)
    prof = next((p for p in reg["profiles"] if p["id"] == id_), None)
    if prof is None:
        raise ValueError("no such profile")
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("a profile needs a name")
        clash = next(
            (p for p in reg["profiles"] if p["id"] != id_ and p["name"].lower() == name.lower()),
            None,
        )
        if clash is not None:
            raise ValueError(f"a profile named {name!r} already exists")
        prof["name"] = name
    if colour:
        prof["colour"] = colour
    save_registry(base, reg)
    return prof


def delete_profile(base: Path, id_: str) -> None:
    """Remove a profile and its data directory. Refuses the default profile (it points at the base
    install). Only ever deletes a directory strictly under base/profiles/ - never the base itself."""
    if id_ == DEFAULT_ID:
        raise ValueError("the default profile cannot be deleted")
    reg = migrate_or_init(base)
    prof = next((p for p in reg["profiles"] if p["id"] == id_), None)
    if prof is None:
        raise ValueError("no such profile")
    d = data_dir_of(base, prof).resolve()
    profiles_root = (base / "profiles").resolve()
    if d != base.resolve() and profiles_root in d.parents:
        shutil.rmtree(d, ignore_errors=True)
    reg["profiles"] = [p for p in reg["profiles"] if p["id"] != id_]
    if reg.get("active") == id_:
        reg["active"] = DEFAULT_ID
    save_registry(base, reg)
