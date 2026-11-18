"""One full scoreboard run, with everything that touches the machine passed in so a test can fake it."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from .machine import Machine
from .protocol import Protocol, require_ac
from .results import build_result, write_result
from .scenarios import run_all


def run(
    models: Sequence[str],
    release: str,
    out_root: Path,
    protocol: Protocol,
    *,
    engine,
    power: str,
    describe_machine: Callable[[str], Machine],
    swap_reader: Callable[[], int | None],
    allow_battery: bool = False,
    only: set[str] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    """Measure every model and write the machine's result file; returns its path.

    Order matters: a missing model is reported before anything runs (nothing is ever pulled), and a machine on
    battery is refused before the first call."""
    digests = engine.require(models)
    require_ac(power, allow_battery)
    machine = describe_machine(power)
    scenarios = {
        tag: {
            "digest": digests[tag],
            "scenarios": run_all(
                engine, tag, protocol, swap_reader, only=only, sleep=sleep, log=log
            ),
        }
        for tag in models
    }
    result = build_result(release, machine, engine.version(), protocol, scenarios)
    return write_result(result, out_root)
