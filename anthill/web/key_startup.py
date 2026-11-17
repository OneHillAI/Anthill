"""Start-up glue for the key layer (anthill/keystore.py): the database probe and checker it needs, and
``ensure_or_exit()`` for the entry points. A missing or untrusted key is a plain message and exit status 1,
never a traceback and never a new key. The database is read directly, read-only, so this works before the
engine exists. Also a tiny command for start.sh: ``python -m anthill.web.key_startup has-values <db>``."""

from __future__ import annotations

import sys
from pathlib import Path

from .. import keystore, stored_values


def _db() -> Path:
    from ..backup import data_paths

    return data_paths()["db"]


def probe() -> bool:
    return stored_values.holds_values(_db())


def check(data_key: bytes) -> bool:
    return stored_values.any_opens(data_key, _db())


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
    except Exception as exc:  # noqa: BLE001 - any unexpected failure is the same plain refusal, never a traceback
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
            return 1 if stored_values.holds_values(Path(argv[1])) else 0
        except keystore.ValuesUnreadable:
            return 2
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
