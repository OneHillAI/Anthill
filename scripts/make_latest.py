#!/usr/bin/env python3
"""Checks for the "Make latest" button: a stable release is promoted from pre-release to latest only when complete.

The Release workflow publishes a new stable release minutes before the app files exist, so for a while "latest"
had an installer package and no app download or update feed (v1.0.1 needed a manual fix). New stable releases now
start as pre-releases, and this decides, from the release as GitHub reports it, whether it is safe to make latest.

Subcommands (used by .github/workflows/make-latest.yml; nothing here talks to the network):

  check <tag> <release.json> <latest.json>
      The release must be a published (not draft) pre-release for a stable tag, hold all six files with the right
      names and sizes, and its update feed (latest.json) must name this version and the bundle in this release.
  runs <runs.json>
      The Release and Desktop release workflow runs for the tag's commit must have completed successfully.

Exit 0 when safe, 1 refused (reasons on stderr).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STABLE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
REQUIRED_RUNS = ("Release", "Desktop release (Tauri auto-update)")


class Refused(Exception):
    pass


def required_assets(version):
    return (
        "Anthill.dmg",
        f"Anthill_{version}_aarch64.dmg",
        "Anthill_aarch64.app.tar.gz",
        "Anthill_aarch64.app.tar.gz.sig",
        "latest.json",
        "Anthill-Appliance.pkg",
    )


def check(tag, release, latest):
    m = STABLE_TAG.match(tag.strip())
    if not m:
        raise Refused(f"'{tag}' is not a stable version tag (vX.Y.Z)")
    version = m.group(1)
    problems = []
    if release.get("tag_name") != tag:
        problems.append(f"the release is for '{release.get('tag_name')}', not {tag}")
    if release.get("draft"):
        problems.append("the release is still a draft")
    if not release.get("prerelease"):
        problems.append("the release is already a full release; there is nothing to make latest")
    assets = {a.get("name"): a for a in release.get("assets") or []}
    for name in required_assets(version):
        a = assets.get(name)
        if a is None:
            problems.append(f"missing file: {name}")
        elif a.get("state", "uploaded") != "uploaded" or not a.get("size"):
            problems.append(f"file not fully uploaded: {name}")
    dmg, versioned = assets.get("Anthill.dmg"), assets.get(f"Anthill_{version}_aarch64.dmg")
    if dmg and versioned and dmg.get("size") != versioned.get("size"):
        problems.append("Anthill.dmg and the versioned dmg differ in size")
    if latest.get("version") != version:
        problems.append(f"the update feed says version '{latest.get('version')}', not {version}")
    platforms = latest.get("platforms") or {}
    if not platforms:
        problems.append("the update feed lists no platform")
    for name, p in platforms.items():
        if f"/download/{tag}/" not in (p.get("url") or ""):
            problems.append(f"the update feed's {name} bundle does not point at this release")
        if not p.get("signature"):
            problems.append(f"the update feed's {name} entry has no signature")
    if problems:
        raise Refused("not safe to make latest: " + "; ".join(problems))
    return True


def runs_green(runs):
    """The newest run of each required workflow for the tag's commit must have completed with success."""
    items = runs.get("workflow_runs", runs) if isinstance(runs, dict) else runs
    newest = {}
    for r in items:
        name = r.get("name")
        if name in REQUIRED_RUNS:
            key = (r.get("created_at") or "", r.get("id") or 0)
            if name not in newest or key >= newest[name][0]:
                newest[name] = (key, r)
    problems = []
    for name in REQUIRED_RUNS:
        r = newest[name][1] if name in newest else None
        if r is None:
            problems.append(f"{name}: no run for this commit")
        elif r.get("status") != "completed" or r.get("conclusion") != "success":
            problems.append(f"{name}: {r.get('status')} / {r.get('conclusion')}")
    if problems:
        raise Refused("the release builds are not finished and green: " + "; ".join(problems))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("tag")
    c.add_argument("release")
    c.add_argument("latest")
    r = sub.add_parser("runs")
    r.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            check(
                a.tag,
                json.loads(Path(a.release).read_text(encoding="utf-8")),
                json.loads(Path(a.latest).read_text(encoding="utf-8")),
            )
        else:
            runs_green(json.loads(Path(a.file).read_text(encoding="utf-8")))
    except Refused as e:
        sys.stderr.write(f"make_latest: refused: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
