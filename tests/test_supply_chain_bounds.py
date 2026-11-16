"""The dependency-bounds rule in pyproject.toml holds, and requirements.lock stays inside it.

pyproject.toml documents that every base runtime dependency carries a floor and an upper bound at the
next major, except `cryptography` and `certifi`, which must accept forward security releases. Nothing
enforced it, and nothing checked that the hash-pinned lock still satisfies what pyproject declares.
These tests pin both, model-free and offline.
"""

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest already pulls tomli there
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

# Intentionally unbounded, with the reason written next to each in pyproject.toml.
UNBOUNDED = {"cryptography", "certifi"}


def _base_deps() -> list[Requirement]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    return [Requirement(s) for s in project["dependencies"]]


def _lock_entries() -> dict[str, tuple[str, str]]:
    """Canonical name -> (pinned version, that package's block of the lock file)."""
    text = (ROOT / "requirements.lock").read_text()
    starts = list(
        re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)", text, re.M)
    )
    entries = {}
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        entries[canonicalize_name(m[1])] = (m[2], text[m.start() : end])
    return entries


def test_base_deps_have_floor_and_ceiling():
    missing = []
    for req in _base_deps():
        ops = {s.operator for s in req.specifier}
        bounded = ">=" in ops and bool(ops & {"<", "<=", "==", "~="})
        if canonicalize_name(req.name) in UNBOUNDED:
            assert ">=" in ops, f"{req.name} needs a floor"
        elif not bounded:
            missing.append(f"{req.name} ({req.specifier or 'no specifier'})")
    assert not missing, (
        "base dependencies need both a floor (>=) and an upper bound (<), see the comment above "
        f"[project] dependencies in pyproject.toml: {missing}"
    )


def test_unbounded_allowlist_is_still_needed():
    names = {canonicalize_name(r.name) for r in _base_deps()}
    assert names >= UNBOUNDED, f"{UNBOUNDED - names} left pyproject.toml; drop them from UNBOUNDED"


def test_lock_pins_satisfy_pyproject():
    lock = _lock_entries()
    problems = []
    for req in _base_deps():
        if req.marker is not None and not req.marker.evaluate():
            continue  # the lock is universal; a marker-gated dep may be absent for this platform
        name = canonicalize_name(req.name)
        if name not in lock:
            problems.append(
                f"{req.name}: declared in pyproject.toml but not pinned in requirements.lock"
            )
        elif not req.specifier.contains(lock[name][0], prereleases=True):
            problems.append(f"{req.name}=={lock[name][0]} in the lock is outside '{req.specifier}'")
    assert not problems, (
        "requirements.lock is out of step with pyproject.toml (recompile it):\n"
        + "\n".join(problems)
    )


def test_every_lock_pin_has_hashes():
    unhashed = [n for n, (_, block) in _lock_entries().items() if "--hash=sha256:" not in block]
    assert not unhashed, f"requirements.lock pins without a sha256 hash: {unhashed}"


@pytest.mark.parametrize("bad", ["requests", "requests>=2", "requests<3"])
def test_bound_check_rejects_one_sided_specifiers(bad):
    ops = {s.operator for s in Requirement(bad).specifier}
    assert not (">=" in ops and bool(ops & {"<", "<=", "==", "~="}))
