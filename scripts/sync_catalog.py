#!/usr/bin/env python3
"""Reconcile the vendored connector catalog against the official MCP Registry.

The catalog (`anthill/connectors/catalog.json`) is curated by humans and merged through a
normal reviewed PR. This script does NOT write to the shipped app: it fetches the official
registry, compares it to our entries, and prints a drift report so a maintainer can open a
PR. That keeps two invariants: the app has no runtime registry dependency (offline-safe),
and no upstream change reaches users without human review.

Usage:
    python scripts/sync_catalog.py            # human-readable report
    python scripts/sync_catalog.py --json     # machine-readable changes

The diff core (`build_index`, `diff_catalog`) is pure and unit-tested in
tests/test_connector_catalog.py; the network fetch is the only impure part.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers?limit=200"
CATALOG_PATH = Path(__file__).resolve().parent.parent / "anthill" / "connectors" / "catalog.json"


def build_index(servers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index registry records by their canonical name (namespace/name)."""
    index: dict[str, dict[str, Any]] = {}
    for s in servers:
        name = s.get("name")
        if isinstance(name, str) and name:
            index[name] = s
    return index


def _registry_remote_url(record: dict[str, Any]) -> str:
    for r in record.get("remotes") or []:
        if isinstance(r, dict) and r.get("url"):
            return str(r["url"])
    return ""


def diff_catalog(
    local: list[dict[str, Any]], index: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Compare our curated entries to the registry index. Pure; returns a list of changes.

    Change kinds:
      missing_upstream  - our `upstream` is no longer in the registry (re-check it)
      endpoint_changed  - an http entry's registry remote URL differs from our endpoint
    """
    changes: list[dict[str, str]] = []
    for e in local:
        upstream = e.get("upstream") or ""
        if not upstream:
            continue
        record = index.get(upstream)
        if record is None:
            changes.append(
                {"id": str(e.get("id", "")), "kind": "missing_upstream", "detail": upstream}
            )
            continue
        if e.get("transport") == "http" and e.get("endpoint"):
            reg_url = _registry_remote_url(record)
            if reg_url and reg_url.rstrip("/") != str(e["endpoint"]).rstrip("/"):
                changes.append(
                    {
                        "id": str(e.get("id", "")),
                        "kind": "endpoint_changed",
                        "detail": f"catalog={e['endpoint']} registry={reg_url}",
                    }
                )
    return changes


def fetch_registry(url: str = REGISTRY_URL) -> list[dict[str, Any]]:
    """Fetch the official registry server list (the only impure part)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    servers = payload.get("servers") if isinstance(payload, dict) else payload
    return servers if isinstance(servers, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit changes as JSON")
    args = ap.parse_args()

    local = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    try:
        servers = fetch_registry()
    except Exception as exc:  # network/registry down: report, do not crash CI
        print(f"could not reach the MCP registry: {exc}", file=sys.stderr)
        return 0
    changes = diff_catalog(local, build_index(servers))

    if args.json:
        print(json.dumps(changes, indent=2))
        return 0
    if not changes:
        print(f"catalog is in sync with the registry ({len(local)} connectors).")
        return 0
    print(f"{len(changes)} item(s) need a curator's attention:\n")
    for c in changes:
        print(f"  [{c['kind']}] {c['id']}: {c['detail']}")
    print("\nEdit anthill/connectors/catalog.json and open a PR (the curation gate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
