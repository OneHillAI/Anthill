"""Measure this machine against a local Ollama and write its scoreboard file (#236).

    python tools/bench/run_scoreboard.py --model qwen2.5:3b

Needs a running Ollama with the model already installed: it never pulls or deletes a model and changes no machine
setting. Plug the machine in first (it refuses to run on battery unless told to) and leave it alone while it runs:
five scenarios, each repeated with a 20 s cool-down, take about ten minutes per model. The result goes to
benchmarks/scoreboard/<release>/<machine>.json. Compare two with tools/bench/compare_scoreboard.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from machinebench import machine as machine_mod
from machinebench.engine import DEFAULT_URL, EngineError, OllamaEngine
from machinebench.protocol import PowerError, Protocol, power_state
from machinebench.run import run

REPO = Path(__file__).resolve().parents[2]


def _args(argv: list[str] | None) -> argparse.Namespace:
    from anthill import __version__

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model",
        action="append",
        help="installed model tag (repeat for several); default qwen2.5:3b",
    )
    ap.add_argument("--base-url", default=DEFAULT_URL)
    ap.add_argument(
        "--release", default=__version__, help="the Anthill release this result belongs to"
    )
    ap.add_argument("--out", type=Path, default=REPO / "benchmarks" / "scoreboard")
    ap.add_argument("--cooldown", type=float, default=Protocol.cooldown_s)
    ap.add_argument("--repeats", type=int, default=Protocol.repeats)
    ap.add_argument(
        "--allow-battery", action="store_true", help="run on battery; the result records it"
    )
    ap.add_argument(
        "--only",
        help="comma-separated scenarios (depth_0, depth_4k, wiki_cold, wiki_warm, cold_load, memory_swap)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    protocol = Protocol(cooldown_s=args.cooldown, repeats=args.repeats)
    try:
        path = run(
            args.model or ["qwen2.5:3b"],
            args.release,
            args.out,
            protocol,
            engine=OllamaEngine(args.base_url),
            power=power_state(),
            describe_machine=machine_mod.describe,
            swap_reader=machine_mod.swap_outs,
            allow_battery=args.allow_battery,
            only=set(args.only.split(",")) if args.only else None,
        )
    except (EngineError, PowerError) as why:
        print(f"error: {why}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
