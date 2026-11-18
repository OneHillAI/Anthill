"""Compare two scoreboard files and list every median that got worse by more than 15% (#236). Report-only: the
exit status is 0 whatever it finds, so it can be run in CI or by hand without ever failing a build.

    python tools/bench/compare_scoreboard.py benchmarks/scoreboard/1.1.1/apple-m4-16gb.json new.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from machinebench.results import (
    REGRESSION_THRESHOLD,
    compare,
    format_changes,
    load_result,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--threshold", type=float, default=REGRESSION_THRESHOLD)
    args = ap.parse_args(argv)
    old, new = load_result(args.old), load_result(args.new)
    print(format_changes(compare(old, new, args.threshold), old, new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
