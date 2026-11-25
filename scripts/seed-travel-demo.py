#!/usr/bin/env python3
"""Seed realistic, non-sensitive Rome/Europe travel dummy data for the Anthill P2P demo.

Designed for 2-node (Mozilla demo: Raul + Christoph) and 3-node (Raul + Christoph + Peter)
peer-to-peer pilot sessions.

Usage (from the Anthill repo root):
  .venv/bin/python scripts/seed-travel-demo.py --persona christoph --wiki-root ~/.anthill-p2p-pilot/wikis

Personas:
  raul      - Trip planner / host: baseline Rome notes + locked Personal/Sensitive canary page.
  christoph - Local Rome & neighborhood guide (area:travel): Trastevere/Testaccio, Fiumicino trains,
              museum timing & Monday closures.
  peter     - Rail & day-trip specialist (area:travel): Italo/Trenitalia 60-day booking windows,
              day trips from Roma Termini (Orvieto, Ostia Antica, Tivoli), August Ferragosto tips.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anthill.wiki import okf  # noqa: E402
from anthill.wiki.workspace import Workspace, workspace_for  # noqa: E402

PERSONA_PAGES: dict[str, list[dict[str, object]]] = {
    "raul": [
        {
            "slug": "rome-trip-outline",
            "title": "Rome May Trip Outline (4 Days)",
            "tags": ["area:travel"],
            "body": (
                "# Rome May Trip Outline (4 Days)\n\n"
                "Draft itinerary for a 4-day spring visit to Rome:\n"
                "- Day 1: Arrive at Fiumicino (FCO), check in near central Rome, evening walk.\n"
                "- Day 2: Colosseum and Roman Forum.\n"
                "- Day 3: Vatican Museums and St. Peter's Basilica.\n"
                "- Day 4: Flexible neighborhood walk or short regional excursion.\n"
            ),
        },
        {
            "slug": "private-passport-and-health",
            "title": "Personal Passport and Medical Notes",
            "tags": ["private", "sensitive", "health"],
            "body": (
                "# Personal Passport and Medical Notes\n\n"
                "Rule 3 privacy boundary test page (locked as Personal / Sensitive - never lent).\n"
                "- Demo canary code: CANARY-PASSPORT-7749-NEVER-LEAK\n"
                "- Travel insurance emergency desk note: keep local only.\n"
            ),
        },
    ],
    "christoph": [
        {
            "slug": "rome-fiumicino-airport-trains",
            "title": "Getting from Fiumicino (FCO) to Central Rome",
            "tags": ["area:travel"],
            "body": (
                "# Getting from Fiumicino (FCO) to Central Rome\n\n"
                "- **Leonardo Express (EUR 14, 32 min non-stop):** Best only if staying near Roma Termini.\n"
                "- **Regional FL1 train (EUR 8, every 15 min):** Stops at Roma Trastevere, Ostiense, "
                "and Tiburtina. Faster and half the price if staying in Trastevere or Testaccio.\n"
                "- **Official white taxis:** Fixed fare EUR 55 inside the Aurelian Walls (confirm the "
                "official City of Rome sticker on the door; skip unsolicited drivers in arrivals).\n"
            ),
        },
        {
            "slug": "rome-neighborhoods-and-booking-windows",
            "title": "Rome Neighborhoods and Museum Booking Windows",
            "tags": ["area:travel"],
            "body": (
                "# Rome Neighborhoods and Museum Booking Windows\n\n"
                "- **Where to stay:** Testaccio and Monti stay walkable and quieter at night than the "
                "crowded Spanish Steps corridor; Trastevere is lively but pick side streets off Piazza Trilussa.\n"
                "- **Colosseum Arena / Underground:** Official Parco Archeologico tickets release exactly "
                "30 days ahead at the exact time slot; third-party resellers charge 3x.\n"
                "- **Vatican Museums:** Book official timed entry 45-60 days ahead for May and avoid "
                "Wednesdays before noon (Papal Audience crowds) and the last Sunday of the month.\n"
                "- **Monday closures:** Galleria Borghese and many civic museums close on Mondays; "
                "schedule Colosseum/Forum or Vatican for Monday instead.\n"
            ),
        },
    ],
    "peter": [
        {
            "slug": "italy-high-speed-rail-booking",
            "title": "Italy High-Speed Rail Booking (Trenitalia vs Italo)",
            "tags": ["area:travel"],
            "body": (
                "# Italy High-Speed Rail Booking (Trenitalia vs Italo)\n\n"
                "- Compare both **Trenitalia Frecciarossa** and **Italo** on the Rome-Florence-Milan "
                "corridor; booking 45-60 days ahead cuts Super Economy / Low Cost fares by 50-65%.\n"
                "- Regional trains (Regionale) have flat prices and never sell out, so buy those on the "
                "day via the Trenitalia app (digital regional tickets auto-validate before departure).\n"
                "- Avoid arriving or departing on **August 15 (Ferragosto)** when family-run restaurants "
                "and neighborhood shops close across central Rome.\n"
            ),
        },
        {
            "slug": "rome-day-trips-by-train",
            "title": "Easy Day Trips from Roma Termini by Train",
            "tags": ["area:travel"],
            "body": (
                "# Easy Day Trips from Roma Termini by Train\n\n"
                "- **Ostia Antica (35 min via Roma-Lido train from Piramide):** Well-preserved Roman port "
                "ruins with shade trees and a fraction of Pompeii's crowds.\n"
                "- **Orvieto (75 min regional train from Termini + 2-min funicular):** Hilltop Umbrian "
                "duomo, underground Etruscan caves, and walkable historic center.\n"
                "- **Tivoli - Villa d'Este (60 min regional train from Tiburtina):** Renaissance fountains; "
                "closed Mondays like Galleria Borghese.\n"
            ),
        },
    ],
}


def seed_persona(persona: str, *, wiki_root: Path, user_id: int) -> list[Path]:
    if persona not in PERSONA_PAGES:
        raise ValueError(f"Unknown persona {persona!r}. Choose from: {', '.join(PERSONA_PAGES)}")
    os.environ["ANTHILL_WIKI_ROOT"] = str(wiki_root)
    ws: Workspace = workspace_for("personal", user_id=user_id)
    ws.init()
    written: list[Path] = []
    for item in PERSONA_PAGES[persona]:
        slug = str(item["slug"])
        body = str(item["body"])
        tags = [str(t) for t in item["tags"]]  # type: ignore[attr-defined]
        path = ws.write_page(slug, body, review="approved")
        parsed = okf.parse_okf(path.read_text(encoding="utf-8"))
        parsed.tags = tags
        path.write_text(okf.to_okf(parsed), encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed realistic travel dummy pages into a pilot Anthill personal wiki."
    )
    parser.add_argument(
        "--persona",
        choices=sorted(PERSONA_PAGES),
        required=True,
        help="Which demo node persona to seed (raul, christoph, or peter).",
    )
    parser.add_argument(
        "--wiki-root",
        type=Path,
        default=Path.home() / ".anthill-p2p-pilot" / "wikis",
        help="Path to ANTHILL_WIKI_ROOT (default: ~/.anthill-p2p-pilot/wikis).",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="Local user ID (default: 1, the first account created on /setup).",
    )
    args = parser.parse_args()
    written = seed_persona(args.persona, wiki_root=args.wiki_root, user_id=args.user_id)
    print(f"Seeded {len(written)} OKGF page(s) for persona '{args.persona}' in {args.wiki_root}:")
    for p in written:
        print(f"  - {p.name}")
    print("")
    print("Next step in Anthill Desktop:")
    print("  1. Open Peers & Hills -> Knowledge lending.")
    print("  2. Toggle 'Travel' to Lent for your shared Hill (leave Personal / Sensitive Locked).")
    print(
        '  3. Try consulting: "I am planning a 4-day trip to Rome in May - where should I stay, '
        'how do I get from Fiumicino airport, and what needs advance booking?"'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
