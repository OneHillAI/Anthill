"""Conversational task creation: NL → {title, schedule, goal} (mocked backend)."""

from anthill.agent import taskgen


class _Backend:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply


def test_parses_clean_json():
    b = _Backend(
        '{"title": "Email digest", "schedule": "09:00", '
        '"goal": "Summarise unread email and post to Slack."}'
    )
    t = taskgen.parse_task("every morning at 9 summarise my email", b)
    assert t == {
        "title": "Email digest",
        "schedule": "09:00",
        "goal": "Summarise unread email and post to Slack.",
    }


def test_json_embedded_in_prose():
    b = _Backend(
        'Here you go:\n{"title":"Weekly report","schedule":"weekly","goal":"Write it."}\nEnjoy'
    )
    t = taskgen.parse_task("weekly report", b)
    assert t["title"] == "Weekly report" and t["schedule"] == "weekly"


def test_falls_back_to_description_on_bad_output():
    t = taskgen.parse_task("do the thing every day", _Backend("not json at all"))
    assert t["goal"] == "do the thing every day"
    assert t["schedule"] == "daily"  # inferred from the description text
    assert t["title"]  # non-empty


def test_falls_back_on_backend_error():
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("model down")

    t = taskgen.parse_task("clean the inbox hourly", _Boom())
    assert t["schedule"] == "hourly" and t["goal"] == "clean the inbox hourly"


def test_long_description_truncates_title():
    desc = "x" * 200
    t = taskgen.parse_task(desc, _Backend("garbage"))
    assert len(t["title"]) <= 120 and t["title"].endswith("…")


def test_normalize_schedule():
    n = taskgen.normalize_schedule
    assert n("once") == "once"
    assert n("Weekly") == "weekly"
    assert n("every hour") == "hourly"
    assert n("daily at 08:30") == "08:30"  # time extracted → daily-at-time
    assert n("7:05") == "07:05"  # zero-padded
    assert n("25:99") == "once"  # invalid time → safe default
    assert n("whenever") == "once"
    # weekday-only constraint is preserved, not collapsed to "daily" (issue #394)
    assert n("every weekday at 9am") == "09:00 weekdays"
    assert n("each business day") == "weekdays"
    assert n("weekdays at 08:30") == "08:30 weekdays"
    assert n("Monday to Friday") == "weekdays"
    assert n("weekly") == "weekly"  # not confused with weekdays
    assert n("9pm") == "21:00"  # am/pm parsed


def test_next_run_weekdays_skips_weekends():
    from datetime import datetime, timedelta, timezone

    from anthill.web.scheduler import _next_run, _normalize_task_schedule

    sat = datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)
    assert sat.weekday() == 5  # sanity: this IS a Saturday
    assert _next_run("weekdays", from_dt=sat).weekday() == 0  # -> Monday (skip Sunday)
    nxt = _next_run("09:00 weekdays", from_dt=sat)
    assert nxt.weekday() == 0 and nxt.hour == 9  # -> Monday 09:00
    legacy = " 09:00 \t weekdays "
    assert _normalize_task_schedule(legacy) == "09:00 weekdays"
    assert _next_run(legacy, from_dt=sat) == nxt
    assert _next_run("daily", from_dt=sat) == sat + timedelta(days=1)  # daily unaffected (runs Sun)


def test_next_run_uses_task_timezone_for_wall_clock_schedules():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    now = datetime(2026, 7, 11, 8, 30, tzinfo=timezone.utc)
    assert _next_run("09:00", from_dt=now, timezone_name="Europe/Madrid") == datetime(
        2026, 7, 12, 7, 0, tzinfo=timezone.utc
    )
    assert _next_run("09:00", from_dt=now, timezone_name="America/New_York") == datetime(
        2026, 7, 11, 13, 0, tzinfo=timezone.utc
    )


def test_daily_and_weekly_keep_local_wall_time_across_dst():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    before_spring_change = datetime(2026, 3, 28, 8, 0, tzinfo=timezone.utc)
    assert _next_run(
        "daily", from_dt=before_spring_change, timezone_name="Europe/Madrid"
    ) == datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)

    week_before_spring_change = datetime(2026, 3, 22, 8, 0, tzinfo=timezone.utc)
    assert _next_run(
        "weekly", from_dt=week_before_spring_change, timezone_name="Europe/Madrid"
    ) == datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)


def test_weekly_schedule_honors_anchor_weekday():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    tuesday = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
    monday_anchor = datetime(2026, 8, 24, 9, 0)
    expected = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
    assert (
        _next_run(
            "weekly",
            from_dt=tuesday,
            timezone_name="UTC",
            schedule_anchor=monday_anchor,
        )
        == expected
    )
    assert (
        _next_run(
            "weekly",
            from_dt=tuesday,
            timezone_name="",
            schedule_anchor=monday_anchor,
        )
        == expected
    )


def test_weekly_schedule_skips_past_first_fold_during_fall_back():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    assert _next_run(
        "weekly",
        from_dt=datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc),
        timezone_name="America/New_York",
        schedule_anchor=datetime(2026, 10, 25, 1, 30),
    ) == datetime(2026, 11, 8, 6, 30, tzinfo=timezone.utc)


def test_fixed_time_skips_the_second_fold_during_fall_back():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    assert _next_run(
        "01:30",
        from_dt=datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc),
        timezone_name="America/New_York",
    ) == datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)


def test_calendar_schedule_uses_today_when_anchor_is_still_future():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    for schedule in ("daily", "weekdays"):
        assert _next_run(
            schedule,
            from_dt=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
            timezone_name="America/New_York",
            schedule_anchor=datetime(2026, 8, 17, 9, 0),
        ) == datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)


def test_fixed_time_uses_future_gap_adjusted_instant():
    from datetime import datetime, timezone

    from anthill.web.scheduler import _next_run

    assert _next_run(
        "02:30",
        from_dt=datetime(2026, 3, 8, 7, 15, tzinfo=timezone.utc),
        timezone_name="America/New_York",
    ) == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)


def test_invalid_timezone_uses_legacy_utc_fallback():
    from anthill.web.scheduler import _normalize_timezone

    assert _normalize_timezone("Europe/Madrid") == "Europe/Madrid"
    assert _normalize_timezone("../../etc/passwd") == ""
    assert _normalize_timezone("") == ""


def test_schedule_normalized_from_model_phrase():
    b = _Backend('{"title":"t","schedule":"every week","goal":"g"}')
    assert taskgen.parse_task("d", b)["schedule"] == "weekly"
