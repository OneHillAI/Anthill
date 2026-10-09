#!/usr/bin/env python3
"""Add the Windows entry to a release's latest.json, the update manifest installed apps read.

    python scripts/windows_release_manifest.py --latest old/latest.json --version 1.2.3 \
        --url https://github.com/OneHillAI/Anthill/releases/download/v1.2.3/Anthill_1.2.3_x64-setup.exe \
        --signature-file Anthill_1.2.3_x64-setup.exe.sig --out new/latest.json

The macOS job publishes the release and its latest.json first; the Windows job adds its own entry afterwards, and only
after its installer has been built, install-checked and uploaded. Doing it here, and not letting a release action rewrite
the manifest while it builds, means a Windows build that fails a check never leaves a Windows entry in the manifest.

Refuses (exit 1, reason on stderr) unless the manifest is the macOS job's for this same version, so a stale or foreign
manifest is never published. The merging is a plain function, tested without a network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WINDOWS_KEYS = ("windows-x86_64", "windows-x86_64-nsis")


class Refused(Exception):
    pass


def merge(latest: dict, *, version: str, url: str, signature: str) -> dict:
    """``latest`` with the Windows entries added; ``latest`` itself is not changed."""
    if latest.get("version") != version:
        raise Refused(f"latest.json is for version {latest.get('version')!r}, not {version!r}")
    platforms = latest.get("platforms")
    if not isinstance(platforms, dict) or not any(key.startswith("darwin") for key in platforms):
        raise Refused("latest.json has no macOS entry; the macOS job must publish the release first")
    signature = signature.strip()
    if not signature:
        raise Refused("the installer's signature is empty")
    if not url.startswith("https://"):
        raise Refused("the installer URL must be https")
    entry = {"signature": signature, "url": url}
    merged = {**latest, "platforms": {**platforms}}
    for key in WINDOWS_KEYS:
        merged["platforms"][key] = dict(entry)
    return merged


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--latest", required=True, help="the release's current latest.json")
    parser.add_argument("--version", required=True, help="the release version without the v (1.2.3)")
    parser.add_argument("--url", required=True, help="where the installer is on the release")
    parser.add_argument("--signature-file", required=True, help="the installer's .sig file")
    parser.add_argument("--out", required=True, help="where to write the new latest.json")
    args = parser.parse_args(argv)
    try:
        latest = json.loads(Path(args.latest).read_text(encoding="utf-8"))
        merged = merge(
            latest,
            version=args.version,
            url=args.url,
            signature=Path(args.signature_file).read_text(encoding="utf-8"),
        )
    except (Refused, OSError, ValueError) as error:
        print(f"windows-release-manifest: REFUSED - {error}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"windows-release-manifest: OK - added {', '.join(WINDOWS_KEYS)} for {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
