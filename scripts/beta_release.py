#!/usr/bin/env python3
"""Logic for the beta release lane, kept out of the workflow YAML so it can be tested.

Subcommands (used by .github/workflows/desktop-beta.yml and desktop-promote.yml):

  check      --version 1.1.0-rc.2 --stable 1.0.0 --tags "$(git tag -l)"
             The version must be X.Y.Z-rc.N, its base X.Y.Z must be higher than the current stable
             version, no tag for it may exist, and N must be exactly one more than the highest rc already
             tagged for that base (1 if none). Exit 0 ok, 1 refused (reason on stderr).
  stable-version [--root .]
             Print the current stable version (anthill/__init__.py __version__).
  apply      --version 1.1.0-rc.2 [--root .]
             Patch the version in src-tauri/tauri.conf.json, src-tauri/Cargo.toml and anthill/__init__.py
             in the CHECKOUT (never committed), so the beta app is built, updates and displays as the rc.
  ci-green   <check-runs.json> [--required lint,test (3.10),...]
             Every required check must have a completed, successful run on the commit. Exit 0 or 1.
  release-files-only <changed-paths-file>
             Every path changed between the beta commit and the release commit must be a release file
             (changelog, version bumps, assembled fragments). Exit 0 or 1, listing the offenders.

Nothing here talks to the network or runs a build.
"""

import argparse
import json
import re
import sys
from pathlib import Path

RC_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-rc\.(\d+)$")
STABLE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# The checks every commit on main carries. `intake` is deliberately not here: it is a pull-request check (it
# gates the PR before it merges) and a merge commit on main has no run of it.
REQUIRED_CHECKS = ("lint", "test (3.10)", "test (3.11)", "test (3.12)", "test (3.13)")

# What a release cut may change after a beta was tested (CHANGELOG assembled, versions bumped, fragments
# consumed). Anything else means code moved since the beta and a new beta is needed.
RELEASE_FILES = (
    "CHANGELOG.md",
    "pyproject.toml",
    "anthill/__init__.py",
    "src-tauri/tauri.conf.json",
    "src-tauri/Cargo.toml",
    "src-tauri/Cargo.lock",
)
RELEASE_PREFIXES = ("changelog.d/",)


class Refused(Exception):
    pass


def parse_rc(version):
    m = RC_RE.match(version.strip())
    if not m:
        raise Refused(f"'{version}' is not a beta version; use X.Y.Z-rc.N, for example 1.1.0-rc.1")
    major, minor, patch, n = (int(x) for x in m.groups())
    if n < 1:
        raise Refused("the rc number starts at 1")
    return (major, minor, patch), n


def parse_stable(version):
    m = STABLE_RE.match(version.strip().lstrip("v"))
    if not m:
        raise Refused(f"'{version}' is not a stable version (X.Y.Z)")
    return tuple(int(x) for x in m.groups())


def check(version, stable, tags):
    base, n = parse_rc(version)
    if base <= parse_stable(stable):
        raise Refused(
            f"the beta's base {'.'.join(map(str, base))} must be higher than the current stable {stable}"
        )
    tag_set = {t.strip() for t in tags if t.strip()}
    if f"v{version}" in tag_set:
        raise Refused(f"v{version} already exists")
    stable_tag = "v" + ".".join(map(str, base))
    if stable_tag in tag_set:
        raise Refused(f"{stable_tag} is already released; pick a higher base version")
    prefix = "v" + ".".join(map(str, base)) + "-rc."
    seen = []
    for t in tag_set:
        if t.startswith(prefix) and t[len(prefix) :].isdigit():
            seen.append(int(t[len(prefix) :]))
    expected = (max(seen) if seen else 0) + 1
    if n != expected:
        raise Refused(f"the next beta for this version is rc.{expected}, not rc.{n}")
    return True


def apply_version(version, root="."):
    parse_rc(version)
    root = Path(root)
    patches = (
        ("src-tauri/tauri.conf.json", r'("version"\s*:\s*)"[^"]+"', rf'\g<1>"{version}"'),
        ("src-tauri/Cargo.toml", r'(?m)^version = "[^"]+"', f'version = "{version}"'),
        ("anthill/__init__.py", r'(?m)^__version__ = "[^"]+"', f'__version__ = "{version}"'),
    )
    for rel, pattern, repl in patches:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new, count = re.subn(pattern, repl, text, count=1)
        if count != 1:
            raise Refused(f"could not find the version in {rel}")
        path.write_text(new, encoding="utf-8")


def stable_version(root="."):
    text = (Path(root) / "anthill/__init__.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^__version__ = "([^"]+)"', text)
    if not m:
        raise Refused("could not find __version__ in anthill/__init__.py")
    return m.group(1)


def ci_green(check_runs, required=REQUIRED_CHECKS):
    """Every required check must have at least one run, and the latest run of each must have succeeded."""
    runs = check_runs.get("check_runs", check_runs) if isinstance(check_runs, dict) else check_runs
    latest = {}
    for r in runs:
        name = r.get("name")
        if name in required:
            # Newest run wins: by start time, and by id when two runs share a start time (a re-run).
            key = (r.get("started_at") or "", r.get("id") or 0)
            if name not in latest or key >= latest[name][0]:
                latest[name] = (key, r)
    problems = []
    for name in required:
        r = latest[name][1] if name in latest else None
        if r is None:
            problems.append(f"{name}: no run on this commit")
        elif r.get("status") != "completed" or r.get("conclusion") != "success":
            problems.append(f"{name}: {r.get('status')} / {r.get('conclusion')}")
    if problems:
        raise Refused("required checks are not all green: " + "; ".join(problems))
    return True


def release_files_only(paths):
    bad = []
    for p in (x.strip() for x in paths if x.strip()):
        if p in RELEASE_FILES or p.startswith(RELEASE_PREFIXES):
            continue
        bad.append(p)
    if bad:
        raise Refused(
            "code or docs changed since the beta was tested, so it must be re-tested as a new beta: "
            + ", ".join(sorted(bad)[:10])
        )
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--version", required=True)
    c.add_argument("--stable", required=True)
    c.add_argument("--tags", default="")
    s_ = sub.add_parser("stable-version")
    s_.add_argument("--root", default=".")
    a_ = sub.add_parser("apply")
    a_.add_argument("--version", required=True)
    a_.add_argument("--root", default=".")
    g = sub.add_parser("ci-green")
    g.add_argument("file")
    g.add_argument("--required", default=",".join(REQUIRED_CHECKS))
    r = sub.add_parser("release-files-only")
    r.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            check(a.version, a.stable, a.tags.splitlines() or a.tags.split())
        elif a.cmd == "stable-version":
            print(stable_version(a.root))
        elif a.cmd == "apply":
            apply_version(a.version, a.root)
        elif a.cmd == "ci-green":
            ci_green(
                json.loads(Path(a.file).read_text(encoding="utf-8")),
                tuple(x for x in a.required.split(",") if x),
            )
        else:
            release_files_only(Path(a.file).read_text(encoding="utf-8").splitlines())
    except Refused as e:
        sys.stderr.write(f"beta_release: refused: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
