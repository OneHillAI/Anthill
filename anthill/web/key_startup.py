"""Start-up glue for the key layer (anthill/keystore.py): the database probe and checker it needs, and
``ensure_or_exit()`` for the entry points. A missing or untrusted key is a plain message and exit status 1,
never a traceback and never a new key. The database is read directly, read-only, so this works before the
engine exists. Also a tiny command for start.sh: ``python -m anthill.web.key_startup has-values <db>``."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from .. import keystore
from . import crypto

# A council member (org_settings.org_council_members, JSON) keeps copies of the lead's encrypted keys.
_MEMBER_FIELDS = ("model_key_enc", "provision_key_enc", "hf_token_enc", "tunnel_key_enc")
_CHECK_CAP = 2000  # values tried before giving up on finding one that opens


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


def stored_values(path: Path) -> Iterator[str]:
    """Every non-empty ``*_enc`` value and council-member copy. A missing file, table or column is simply
    nothing; a database that cannot be read raises ValuesUnreadable (it is not "no values")."""
    if not path.is_file():
        return
    try:
        with closing(
            sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True, timeout=5)
        ) as con:
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
        raise keystore.ValuesUnreadable(
            "Anthill could not read its database to check the key. Nothing was changed."
        ) from None


def _db() -> Path:
    from ..backup import data_paths

    return data_paths()["db"]


def database_holds_values(path: Path) -> bool:
    return next(stored_values(path), None) is not None


def probe() -> bool:
    return database_holds_values(_db())


def check(data_key: bytes) -> bool:
    """True when no value is stored or at least one stored value opens with ``data_key``."""
    seen = 0
    for seen, token in enumerate(stored_values(_db()), start=1):
        try:
            crypto.decrypt_with_key(data_key, token)
            return True
        except Exception:
            if seen >= _CHECK_CAP:
                break
    return seen == 0


def verify_every_value(data_key: bytes, path: Path) -> bool:
    """True when EVERY stored value in the database at ``path`` opens with ``data_key`` (none is fine)."""
    try:
        for token in stored_values(path):
            crypto.decrypt_with_key(data_key, token)
    except keystore.ValuesUnreadable:
        raise
    except Exception:
        return False
    return True


def register() -> None:
    keystore.set_data_hooks(probe, check)


register()


def ensure_or_exit() -> None:
    """Run the start-up decision table. Print any note; on a refusal print one plain message and exit 1."""
    register()
    try:
        notes = keystore.ensure_ready()
    except keystore.KeystoreError as exc:
        reason = str(exc)
    except Exception as exc:
        reason = f"Anthill could not read or open its key ({type(exc).__name__})."
    else:
        for note in notes:
            print(note, file=sys.stderr, flush=True)
        return
    keystore.log.error("key layer: start-up refused")
    print(
        f"Anthill did not start. {reason}\nNothing was changed, and no new key was made.",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    """``has-values <db>``: exit 0 none stored, 1 values stored, 2 the database cannot be read."""
    if len(argv) == 2 and argv[0] == "has-values":
        try:
            return 1 if database_holds_values(Path(argv[1])) else 0
        except keystore.ValuesUnreadable:
            return 2
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
