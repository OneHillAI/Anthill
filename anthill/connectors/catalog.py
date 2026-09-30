"""Loader + helpers for the vendored connector catalog (`catalog.json`).

The JSON is the single source of truth, edited by humans (or proposed by
`scripts/sync_catalog.py`) through a normal reviewed PR. We read it relative to this
module so it resolves both in a dev checkout and inside the PyInstaller bundle (the spec
ships `anthill/connectors/catalog.json` as a data file).

Entry shape (see catalog.json for live examples)::

    {
      "id": "filesystem",              # stable slug, unique
      "name": "Filesystem",
      "category": "documents",         # documents|communication|development|database|web
      "summary": "one line, admin-facing",
      "tier": "verified",              # verified | community
      "upstream": "io.modelcontextprotocol/filesystem",  # ref into the official registry
      "transport": "stdio",            # http | stdio
      "endpoint": "",                  # http URL (transport == http)
      "command": "npx -y ... {path}",  # stdio argv template (transport == stdio)
      "auth": {
        "type": "none",                # none | token | oauth
        "header": "Authorization: Bearer ",   # token: prefix the value is appended to
        "fields": [                    # inputs the admin fills; substituted client-side
          {"key": "path", "label": "Folder", "placeholder": "/path",
           "required": true, "target": "command"}   # target: command|endpoint|auth
        ]
      },
      "scopes": ["read files", "search files"],   # plain-English, shown to the org
      "provides_files": true,          # doc-source: agents can list/read files (PR2/PR3)
      "setup_url": "https://...",      # provider setup docs (esp. for oauth)
      "note": ""                       # optional caveat shown under the tile
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).with_name("catalog.json")

VALID_CATEGORIES = {"documents", "communication", "development", "database", "web"}
VALID_TIERS = {"verified", "community"}
VALID_TRANSPORTS = {"http", "stdio"}
VALID_AUTH_TYPES = {"none", "token", "oauth"}


def load_catalog() -> list[dict[str, Any]]:
    """Return the curated catalog entries, or [] if the file is missing/unreadable.

    Never raises: a broken catalog must not take down the connectors page (the freeform
    'add a custom server' path still works without it).
    """
    try:
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict) and e.get("id")]


def catalog_by_id(cid: str) -> dict[str, Any] | None:
    """The catalog entry with this id, or None."""
    if not cid:
        return None
    for e in load_catalog():
        if e.get("id") == cid:
            return e
    return None


def doc_source_ids() -> set[str]:
    """Catalog ids of connectors that expose files (used by the wiki/chat/task pickers)."""
    return {str(e["id"]) for e in load_catalog() if e.get("provides_files")}


def validate_entry(e: dict[str, Any]) -> list[str]:
    """Return a list of problems with one catalog entry ([] == valid).

    Used by the catalog test (every shipped entry must pass) and by the sync script
    before it proposes a change.
    """
    problems: list[str] = []
    for field in ("id", "name", "category", "summary", "tier", "transport"):
        if not e.get(field):
            problems.append(f"missing {field}")
    if e.get("category") and e["category"] not in VALID_CATEGORIES:
        problems.append(f"bad category {e['category']!r}")
    if e.get("tier") and e["tier"] not in VALID_TIERS:
        problems.append(f"bad tier {e['tier']!r}")
    transport = e.get("transport")
    if transport and transport not in VALID_TRANSPORTS:
        problems.append(f"bad transport {transport!r}")
    if transport == "http" and not e.get("endpoint"):
        problems.append("http entry has no endpoint")
    if transport == "stdio" and not e.get("command"):
        problems.append("stdio entry has no command")
    auth = e.get("auth") or {}
    if not isinstance(auth, dict):
        problems.append("auth must be an object")
    else:
        atype = auth.get("type", "none")
        if atype not in VALID_AUTH_TYPES:
            problems.append(f"bad auth type {atype!r}")
        for fld in auth.get("fields", []) or []:
            if not isinstance(fld, dict) or not fld.get("key"):
                problems.append("auth field missing key")
                continue
            if fld.get("target") not in (None, "command", "endpoint", "auth", "env"):
                problems.append(f"bad field target {fld.get('target')!r}")
    return problems
