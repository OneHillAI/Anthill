#!/usr/bin/env python3
"""Check that a release holds every file its platforms need, and that the update manifest points at them.

    python scripts/verify_release.py --tag v1.1.0-rc.2 --platform macos --platform windows
    python scripts/verify_release.py --assets assets.json --latest latest.json --platform windows   (offline)

A release is made of several jobs (macOS, Windows), each uploading its own files and adding its own entry to
``latest.json``, the manifest installed apps read. A release where one platform failed looks fine on the page and
breaks that platform's update, so this lists what each platform must have and what is missing. It replaces the
``verify-release.sh`` that docs/releasing.md used to name.

Exit status: 0 = nothing missing, 1 = something missing (listed on stderr), 2 = bad usage.
The checking is plain functions so it is tested without a network; only the command line talks to GitHub (``gh``).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

# What each platform must publish: file name patterns, and the platform keys in latest.json.
PLATFORMS: dict[str, dict[str, tuple[str, ...]]] = {
    "macos": {
        "files": (r"\.dmg$", r"\.app\.tar\.gz$", r"\.app\.tar\.gz\.sig$"),
        "update_keys": ("darwin-aarch64",),
    },
    "windows": {
        "files": (r"-setup\.exe$", r"-setup\.exe\.sig$"),
        "update_keys": ("windows-x86_64",),
    },
}


def asset_names(assets) -> set[str]:
    """Names from a release's assets, given either the API objects or plain names."""
    return {a.get("name", "") if isinstance(a, dict) else str(a) for a in assets}


def problems(assets, latest: dict | None, platforms: list[str]) -> list[str]:
    """Everything missing for ``platforms``; an empty list means the release is complete for them."""
    names = asset_names(assets)
    found: list[str] = []
    if latest is None:
        found.append("no latest.json (the update manifest)")
    for platform in platforms:
        spec = PLATFORMS[platform]
        for pattern in spec["files"]:
            if not any(re.search(pattern, n) for n in names):
                found.append(f"{platform}: no file matching {pattern}")
        for key in spec["update_keys"]:
            entry = ((latest or {}).get("platforms") or {}).get(key)
            if not entry:
                found.append(f"{platform}: latest.json has no '{key}' entry")
                continue
            if not entry.get("signature"):
                found.append(f"{platform}: the '{key}' entry in latest.json has no signature")
            target = urllib.parse.unquote(str(entry.get("url", "")).rsplit("/", 1)[-1])
            if target not in names:
                found.append(f"{platform}: the '{key}' entry points at '{target}', which is not on the release")
    return found


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=120).stdout


def fetch(tag: str, repo: str | None) -> tuple[list[dict], dict | None]:
    """The release's assets and its latest.json, through the ``gh`` command line."""
    repo_args = ["--repo", repo] if repo else []
    path = f"repos/{repo}/releases/tags/{tag}" if repo else f"repos/{{owner}}/{{repo}}/releases/tags/{tag}"
    assets = json.loads(_gh("api", path, "--jq", ".assets"))
    latest = None
    if any(a.get("name") == "latest.json" for a in assets):
        with tempfile.TemporaryDirectory() as folder:
            _gh("release", "download", tag, "--pattern", "latest.json", "--dir", folder, *repo_args)
            latest = json.loads((Path(folder) / "latest.json").read_text(encoding="utf-8"))
    return assets, latest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="the release tag to check (needs the gh command line)")
    parser.add_argument("--repo", help="owner/name; default is the repository of the current folder")
    parser.add_argument("--assets", help="offline: a JSON file of the release's assets (or names)")
    parser.add_argument("--latest", help="offline: the release's latest.json")
    parser.add_argument("--platform", action="append", choices=sorted(PLATFORMS), required=True)
    args = parser.parse_args(argv)
    if bool(args.tag) == bool(args.assets):
        print("give either --tag, or --assets with --latest", file=sys.stderr)
        return 2
    if args.tag:
        assets, latest = fetch(args.tag, args.repo)
    else:
        assets = json.loads(Path(args.assets).read_text(encoding="utf-8"))
        latest = json.loads(Path(args.latest).read_text(encoding="utf-8")) if args.latest else None
    missing = problems(assets, latest, args.platform)
    for line in missing:
        print(f"verify-release: MISSING - {line}", file=sys.stderr)
    if missing:
        return 1
    print(f"verify-release: OK - complete for {', '.join(args.platform)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
