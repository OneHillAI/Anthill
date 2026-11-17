"""The encrypted values stored in an Anthill database, read directly and read-only (so it works before the
engine exists, on a staged copy, and in a snapshot): is there any, and does a key open them. Used by the key
layer's start-up, bring-in, restore and the snapshot check. A top-level module: it never imports the web app."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .keystore import KeystoreError, ValuesUnreadable

# A council member (org_settings.org_council_members, JSON) keeps copies of the lead's encrypted keys.
_MEMBER_FIELDS = ("model_key_enc", "provision_key_enc", "hf_token_enc", "tunnel_key_enc")
CAP = 5000  # values checked at most in one go


class TooManyValues(KeystoreError):
    """More stored values than can be checked in one go."""


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _members(blob: object) -> Iterator[str]:
    try:
        members = json.loads(str(blob))
    except ValueError:
        return
    for member in members if isinstance(members, list) else []:
        if not isinstance(member, dict):
            continue
        for holder in (member, member.get("provider_config")):
            if isinstance(holder, dict):
                for field in _MEMBER_FIELDS:
                    if isinstance(holder.get(field), str) and holder[field]:
                        yield holder[field]


def iter_values(path: Path) -> Iterator[str]:
    """Every non-empty ``*_enc`` value and council-member copy. A missing file, table or column is simply
    nothing; a database that cannot be read raises ValuesUnreadable (it is not "no values")."""
    if not path.is_file():
        return
    try:
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as con:
            tables = [
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
            for table in tables:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({_quote(table)})")]
                for col in (c for c in cols if c.endswith("_enc")):
                    q = _quote(col)
                    query = f"SELECT {q} FROM {_quote(table)} WHERE {q} IS NOT NULL AND {q} != ''"
                    for (value,) in con.execute(query):
                        if isinstance(value, str):
                            yield value
                if table == "org_settings" and "org_council_members" in cols:
                    for (blob,) in con.execute("SELECT org_council_members FROM org_settings"):
                        yield from _members(blob)
    except (sqlite3.Error, OSError):
        raise ValuesUnreadable("Anthill could not read the database to check the key.") from None


def holds_values(path: Path) -> bool:
    return next(iter_values(path), None) is not None


def opens(key: bytes, token: str) -> bool:
    try:
        raw = base64.b64decode(token)
        AESGCM(key).decrypt(raw[:12], raw[12:], None)
        return True
    except (InvalidTag, ValueError, TypeError):  # wrong key, damaged or not base64, not text
        return False


def any_opens(key: bytes, path: Path) -> bool:
    """True when the database holds no value, or at least one opens with ``key``."""
    seen = 0
    for seen, token in enumerate(iter_values(path), start=1):
        if opens(key, token):
            return True
        if seen >= CAP:
            break
    return seen == 0


def all_open(key: bytes, path: Path) -> bool:
    """True when EVERY stored value opens with ``key`` (none is fine). More than CAP values raise TooManyValues."""
    for seen, token in enumerate(iter_values(path), start=1):
        if seen > CAP:
            raise TooManyValues(
                "There are too many saved passwords in this database to check them all."
            )
        if not opens(key, token):
            return False
    return True
