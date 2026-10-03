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
  is-owner   --actor LOGIN [--config .github/release-owners.txt]
             The person starting Promote must be one of `release_owners` in .asdd.yml (empty or missing: nobody).
  promotable --beta v1.1.0-rc.2 --tags "$(git tag -l)"
             Print the stable version a beta would ship as (1.1.0). The beta tag must exist and be a beta, and
             the stable tag for that version must not exist yet.
  tester-pass <statuses.json> --sha SHA
             The test agent's newest recorded verdict on that exact commit (the `asdd/test` commit status, set
             by the workflow's bot) must be success.
  version-only <old-file> <new-file>
             The diff (git diff -U0 beta release -- FILE) may change nothing but a version line.
  beta-built <assets.json>
             The beta release must hold latest.json and a signed .app.tar.gz (what Cut Beta publishes).
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
)
RELEASE_PREFIXES = ("changelog.d/",)
# These two can carry real behaviour (dependencies, code), so a release cut may change only their version line.
VERSION_ONLY_FILES = ("pyproject.toml", "anthill/__init__.py")
VERSION_LINE_RES = (
    re.compile(r'^version\s*=\s*"[^"]+"\s*$'),
    re.compile(r'^__version__\s*=\s*"[^"]+"\s*$'),
)


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


def is_owner(actor, owners_text):
    """True when `actor` is listed in the release-owners file (one login per line, `#` starts a comment,
    case-insensitive). An empty or missing list means nobody can promote."""
    owners = []
    for line in owners_text.splitlines():
        login = line.split("#", 1)[0].strip().lower()
        if login:
            owners.append(login)
    if not owners:
        raise Refused("no release owners are listed, so nobody can promote")
    if actor.strip().lower() not in owners:
        raise Refused(
            f"'{actor}' is not a release owner; only a person listed in .github/release-owners.txt can promote"
        )
    return True


def promotable(beta_tag, tags):
    """The stable version a beta ships as; refuses a missing or non-beta tag, or an already released version."""
    tag = beta_tag.strip()
    if not tag.startswith("v"):
        raise Refused(f"'{beta_tag}' is not a tag; use the beta's tag, for example v1.1.0-rc.2")
    base, _ = parse_rc(tag[1:])
    if tag not in {t.strip() for t in tags}:
        raise Refused(f"the tag {tag} does not exist")
    stable = "v" + ".".join(map(str, base))
    if stable in {t.strip() for t in tags}:
        raise Refused(f"{stable} is already released")
    return ".".join(map(str, base))


def tester_passed(statuses, sha, context="asdd/test", creator="github-actions[bot]"):
    """The test agent's newest recorded verdict on this exact commit must be success. The agent records it as the
    `asdd/test` commit status from its own workflow (a structured signal, not text scraped from a comment)."""
    mine = [
        s
        for s in statuses
        if s.get("context") == context and (s.get("creator") or {}).get("login") == creator
    ]
    if not mine:
        raise Refused(
            f"the test agent has not recorded a verdict on {sha[:12]}; run 'ASDD test' on that commit and wait for it"
        )
    newest = max(mine, key=lambda s: (s.get("created_at") or "", s.get("id") or 0))
    if newest.get("state") != "success":
        raise Refused(
            f"the test agent's newest verdict on {sha[:12]} is '{newest.get('state')}', not success"
        )
    return True


def _blank_first_version_line(text, pattern):
    """The file's lines with its first version line replaced by a placeholder, and whether one was found."""
    out, found = [], False
    for line in text.splitlines():
        if not found and pattern.match(line.strip()):
            out.append("<version line>")
            found = True
        else:
            out.append(line)
    return out, found


def version_only(old_text, new_text):
    """The two versions of a file (the beta commit's and the release commit's) may differ only in their first
    version line. Compared as whole files with that line blanked, so there is no diff to mis-read: any other
    difference, however it is written, makes the two unequal and is refused."""
    for pattern in VERSION_LINE_RES:
        old, found_old = _blank_first_version_line(old_text, pattern)
        new, found_new = _blank_first_version_line(new_text, pattern)
        if found_old and found_new:
            if old != new:
                changed = [n for o, n in zip(old, new, strict=False) if o != n][:3] or [
                    "(lines added or removed)"
                ]
                raise Refused(
                    "only the version line may change in this file between the beta and the release; "
                    "also changed: " + "; ".join(x[:80] for x in changed)
                )
            return True
    raise Refused("could not find the version line in both versions of this file")


def beta_built(assets):
    """The beta release must hold what Cut Beta publishes: the update manifest and the signed updater bundle.
    A hand-made tag, or a run that failed before building, cannot be promoted."""
    names = {a.get("name", "") if isinstance(a, dict) else str(a) for a in assets}
    problems = []
    if "latest.json" not in names:
        problems.append("no latest.json (the update manifest)")
    bundles = sorted(n for n in names if n.endswith(".app.tar.gz"))
    if not bundles:
        problems.append("no updater bundle (.app.tar.gz)")
    elif not any(b + ".sig" in names for b in bundles):
        problems.append("the updater bundle has no signature (.sig)")
    if problems:
        raise Refused("this release was not built by Cut Beta: " + "; ".join(problems))
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
    o = sub.add_parser("is-owner")
    o.add_argument("--actor", required=True)
    o.add_argument("--config", default=".github/release-owners.txt")
    pr_ = sub.add_parser("promotable")
    pr_.add_argument("--beta", required=True)
    pr_.add_argument("--tags", default="")
    t_ = sub.add_parser("tester-pass")
    t_.add_argument("file")
    t_.add_argument("--sha", required=True)
    v_ = sub.add_parser("version-only")
    v_.add_argument("old")
    v_.add_argument("new")
    b_ = sub.add_parser("beta-built")
    b_.add_argument("file")
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
        elif a.cmd == "is-owner":
            is_owner(a.actor, Path(a.config).read_text(encoding="utf-8"))
        elif a.cmd == "promotable":
            print(promotable(a.beta, a.tags.splitlines() or a.tags.split()))
        elif a.cmd == "tester-pass":
            tester_passed(json.loads(Path(a.file).read_text(encoding="utf-8")), a.sha)
        elif a.cmd == "version-only":
            version_only(
                Path(a.old).read_text(encoding="utf-8"), Path(a.new).read_text(encoding="utf-8")
            )
        elif a.cmd == "beta-built":
            beta_built(json.loads(Path(a.file).read_text(encoding="utf-8")))
        else:
            release_files_only(Path(a.file).read_text(encoding="utf-8").splitlines())
    except Refused as e:
        sys.stderr.write(f"beta_release: refused: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
