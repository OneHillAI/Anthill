"""Turn a plain-language request into a structured scheduled task.

Used by the Tasks page's conversational creator: the user types "every morning,
summarise my unread email", and the model returns {title, schedule, goal} that
maps onto a ScheduledTask. Always returns a valid dict - schedule is normalised
to what the scheduler understands (once|hourly|daily|weekly|HH:MM), and title/goal
fall back to the raw description if the model returns nothing useful.
"""

from __future__ import annotations

import re

from ..common.jsonchat import extract_json, json_chat
from ..inference.base import Message

ALLOWED = ("once", "hourly", "daily", "weekly", "weekdays")

_SYS = (
    "Convert the user's request into a scheduled task. Return ONLY a JSON object "
    "with exactly these keys:\n"
    '  "title"    - a short label, at most 60 characters\n'
    '  "schedule" - one of: once, hourly, daily, weekly, weekdays, OR a 24-hour time "HH:MM" '
    'for daily-at-that-time, OR "HH:MM weekdays" for that time on weekdays only (Mon-Fri)\n'
    '  "goal"     - a clear, self-contained instruction an autonomous agent can execute\n'
    "Infer the schedule from phrasing: 'every morning at 9' → \"09:00\"; 'every weekday at 9am' "
    '→ "09:00 weekdays"; \'each business day\' → "weekdays"; \'each week\' → "weekly"; '
    '\'right now\' / one-off → "once". If unclear, use "once".'
)


def parse_task(description: str, backend, *, today: str = "", think: bool | None = None) -> dict:
    """NL description → {title, schedule, goal}. Best-effort; always valid."""
    desc = (description or "").strip()
    data = {}
    try:
        user = desc + (f"\n\n(today is {today})" if today else "")
        msgs = [Message("system", _SYS), Message("user", user)]
        # think is passed only when given, so a call without it is exactly what it was before.
        raw = json_chat(backend, msgs) if think is None else json_chat(backend, msgs, think=think)
        data = extract_json(raw)
    except Exception:
        data = {}
    return _validate(data, desc)


def _validate(data: dict, desc: str) -> dict:
    title = str(data.get("title") or "").strip()
    if not title:
        title = (desc[:57] + "…") if len(desc) > 60 else (desc or "New task")
    goal = str(data.get("goal") or "").strip() or desc
    # Use the model's schedule; if it gave none (bad output / error), infer from
    # the raw description so phrases like "every day" / "hourly" still land.
    sched_raw = str(data.get("schedule") or "").strip()
    schedule = normalize_schedule(sched_raw) if sched_raw else normalize_schedule(desc)
    return {"title": title[:120], "schedule": schedule, "goal": goal}


def normalize_schedule(s: str) -> str:
    """Coerce a model's schedule guess (or the raw request) into a value the scheduler accepts. Preserves
    a weekday-only constraint - "every weekday at 9am" -> "09:00 weekdays", not "daily" (issue #394)."""
    s = (s or "").strip().lower()
    weekdays = bool(
        re.search(r"week\s?days?\b|business day|mon(day)?\s*(?:-|to|through|thru)\s*fri", s)
    )
    time_str = ""
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", s)  # HH:MM
    if m and 0 <= int(m.group(1)) < 24 and 0 <= int(m.group(2)) < 60:
        time_str = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    elif not m:
        ampm = re.search(r"\b(\d{1,2})\s*([ap])\.?m\.?\b", s)  # "9am" / "9 pm"
        if ampm and 1 <= int(ampm.group(1)) <= 12:
            time_str = f"{int(ampm.group(1)) % 12 + (12 if ampm.group(2) == 'p' else 0):02d}:00"
    if weekdays:
        return (f"{time_str} weekdays").strip()  # "09:00 weekdays" or just "weekdays"
    if time_str:
        return time_str
    if s in ALLOWED:
        return s
    if "hour" in s:
        return "hourly"
    if "week" in s:
        return "weekly"
    if "day" in s or "daily" in s:
        return "daily"
    return "once"
