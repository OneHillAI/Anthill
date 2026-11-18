"""The scoreboard file: one JSON per machine per release, and a report-only comparison of two of them."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .machine import Machine
from .protocol import Measurement, Metric, Protocol

SCHEMA = 1
REGRESSION_THRESHOLD = 0.15  # a median that gets worse by more than this is flagged


def metric_dict(m: Metric) -> dict:
    return {
        "median": m.median,
        "spread": None if m.spread is None else round(m.spread, 4),
        "samples": list(m.samples),
        "status": m.status,
    }


def scenario_dict(entry: dict) -> dict:
    measurement: Measurement = entry["measurement"]
    return {
        "params": entry["params"],
        "metrics": {name: metric_dict(m) for name, m in measurement.metrics.items()},
        "notes": list(measurement.notes),
    }


def build_result(
    release: str,
    machine: Machine,
    engine_version: str,
    protocol: Protocol,
    models: dict[str, dict],
    recorded_at: datetime | None = None,
) -> dict:
    """``models``: tag -> {"digest": str, "scenarios": {name: {"params": dict, "measurement": Measurement}}}."""
    when = recorded_at or datetime.now(timezone.utc)
    return {
        "schema": SCHEMA,
        "anthill_release": release,
        "recorded_at": when.isoformat(timespec="seconds"),
        "machine": machine.as_dict(),
        "engine": {"name": "ollama", "version": engine_version},
        "protocol": asdict(protocol),
        "models": {
            tag: {
                "digest": info["digest"],
                "scenarios": {n: scenario_dict(e) for n, e in info["scenarios"].items()},
            }
            for tag, info in models.items()
        },
    }


def result_path(root: Path, release: str, machine_id: str) -> Path:
    return Path(root) / release / f"{machine_id}.json"


def write_result(result: dict, root: Path) -> Path:
    path = result_path(root, result["anthill_release"], result["machine"]["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    return path


def load_result(path: Path | str) -> dict:
    data = json.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA:
        raise ValueError(
            f"{path} is not a scoreboard file this tool reads (schema {data.get('schema')!r})"
        )
    return data


@dataclass(frozen=True)
class Change:
    model: str
    scenario: str
    metric: str
    old: float
    new: float
    change: float  # relative; positive is worse


def _worse_by(name: str, old: float, new: float) -> float | None:
    """How much worse ``new`` is than ``old`` as a share of ``old`` (negative: better), or None for a figure that
    is not compared. Speeds (``*_tok_s``) are better higher; times and swap-outs are better lower."""
    if name.endswith("_tok_s"):
        higher_is_better = True
    elif name.endswith("_ms") or name == "swap_outs":
        higher_is_better = False
    else:
        return None
    if old == 0:
        return float("inf") if (new > 0 and not higher_is_better) else 0.0
    delta = (old - new) / old if higher_is_better else (new - old) / old
    return delta


def _ok_median(metric: dict | None) -> float | None:
    return metric["median"] if metric and metric.get("status") == "ok" else None


def compare(old: dict, new: dict, threshold: float = REGRESSION_THRESHOLD) -> list[Change]:
    """Every median that got worse by more than ``threshold``, between two results of the same schema. Only
    figures that were ``ok`` in both are compared. Report-only: nothing here fails a build."""
    flagged: list[Change] = []
    for tag, info in new["models"].items():
        for scenario, entry in info["scenarios"].items():
            before = (
                old["models"].get(tag, {}).get("scenarios", {}).get(scenario, {}).get("metrics", {})
            )
            for name, metric in entry["metrics"].items():
                a, b = _ok_median(before.get(name)), _ok_median(metric)
                worse = None if a is None or b is None else _worse_by(name, a, b)
                if worse is not None and worse > threshold:
                    flagged.append(Change(tag, scenario, name, a, b, worse))  # type: ignore[arg-type]
    return flagged


def format_changes(changes: list[Change], old: dict, new: dict) -> str:
    head = (
        f"{old['machine']['id']} ({old['anthill_release']}, Ollama {old['engine']['version']}) -> "
        f"{new['machine']['id']} ({new['anthill_release']}, Ollama {new['engine']['version']})"
    )
    if not changes:
        return head + "\nno median got worse by more than the threshold"
    lines = [
        f"{c.model} {c.scenario} {c.metric}: {c.old:g} -> {c.new:g} ({c.change:+.0%} worse)"
        for c in changes
    ]
    return head + "\n" + "\n".join(lines)
