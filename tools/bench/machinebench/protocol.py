"""The measurement protocol: a cool-down, a discarded warm-up, repeats, the median and the spread.

Nothing here knows about an engine. A run is any function that returns named numbers; ``measure`` repeats it the
same way for every scenario so that two results are comparable. A figure whose spread is above ``max_spread`` is
reported as ``unstable``, never quietly kept, and a figure the engine did not report is ``missing``, never 0.
"""

from __future__ import annotations

import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Protocol:
    cooldown_s: float = 20.0  # between runs, so a hot chip does not slow the next one
    repeats: int = 3
    warmups: int = 1  # runs whose numbers are thrown away (load the model, fill the caches)
    max_spread: float = 0.10  # (max - min) / median above this makes a figure `unstable`
    ms_noise_floor: float = (
        5.0  # ... except a time whose whole range is this many ms or less: clock jitter
    )
    token_noise_floor: float = (
        8.0  # ... or a token count whose range is this wide or less (a template header)
    )


@dataclass(frozen=True)
class Metric:
    samples: tuple[float, ...]
    median: float | None
    spread: float | None
    status: str  # "ok" | "unstable" | "missing"


@dataclass(frozen=True)
class Measurement:
    metrics: dict[str, Metric]
    notes: tuple[str, ...] = field(default_factory=tuple)


class RunInvalid(Exception):
    """A run that cannot count (the prompt was the wrong length, the answer stopped early, a cold run hit the
    cache). The reason is kept in the result's notes and the figures of that scenario are marked unstable."""


def spread_of(values: list[float]) -> float | None:
    """(max - min) over the median; None when the median is not positive."""
    median = statistics.median(values)
    return (max(values) - min(values)) / median if median > 0 else None


def summarise(
    values: list[float | None], protocol: Protocol, invalid: bool = False, name: str = ""
) -> Metric:
    """One metric from its repeats. ``missing`` when some repeat did not report it. A time (``*_ms``) whose
    whole range is within ``ms_noise_floor`` is not unstable however large its share of a tiny median: a 45 ms
    first word that reads 41 to 46 is the clock and the engine's rounding, not the machine. The same goes for a
    token count (``*_tokens``) within ``token_noise_floor``: a cold prompt's 3 or 4 cached tokens are the chat
    template's header."""
    got = [v for v in values if v is not None]
    if not got or len(got) < len(values):
        return Metric(tuple(got), statistics.median(got) if got else None, None, "missing")
    spread = spread_of(got)
    jitter = _within_noise(name, max(got) - min(got), protocol)
    unstable = invalid or spread is None or (spread > protocol.max_spread and not jitter)
    return Metric(tuple(got), statistics.median(got), spread, "unstable" if unstable else "ok")


def _within_noise(name: str, spread_range: float, protocol: Protocol) -> bool:
    if name.endswith("_ms"):
        return spread_range <= protocol.ms_noise_floor
    return name.endswith("_tokens") and spread_range <= protocol.token_noise_floor


def measure(
    run: Callable[[], dict[str, float | None]],
    protocol: Protocol,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Measurement:
    """Run ``run`` ``warmups`` times (discarded) then ``repeats`` times with a cool-down before every run after
    the first, and summarise each named number."""
    warm_notes: list[str] = []
    for _ in range(protocol.warmups):
        _try(run, warm_notes)  # a bad warm-up is noted, but its numbers were never going to count
        sleep(protocol.cooldown_s)
    notes: list[str] = []
    runs: list[dict[str, float | None]] = []
    for i in range(protocol.repeats):
        if i:
            sleep(protocol.cooldown_s)
        result = _try(run, notes)
        if result is not None:
            runs.append(result)
    names = sorted({k for r in runs for k in r})
    invalid = bool(notes) or len(runs) < protocol.repeats
    return Measurement(
        {n: summarise([r.get(n) for r in runs], protocol, invalid, n) for n in names},
        tuple(warm_notes + notes),
    )


def _try(
    run: Callable[[], dict[str, float | None]], notes: list[str]
) -> dict[str, float | None] | None:
    try:
        return run()
    except RunInvalid as why:
        notes.append(str(why))
        return None


class PowerError(RuntimeError):
    """The machine is on battery: a laptop on battery slows itself down, so the numbers would not compare."""


def power_state(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """``"ac"``, ``"battery"`` or ``"unknown"`` (macOS ``pmset -g batt``; other systems are ``unknown``)."""
    try:
        out = run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if "AC Power" in out:
        return "ac"
    return "battery" if "Battery Power" in out else "unknown"


def require_ac(state: str, allow_battery: bool = False) -> None:
    if state == "battery" and not allow_battery:
        raise PowerError(
            "This machine is running on battery. Plug it in, or pass --allow-battery and the result will say so."
        )
